"""Decoding CryEngine binary XML ("CryXmlB"), which the game uses for its config files.

Layout: the magic "CryXmlB\\0", a header of nine little-endian uint32s, then four
tables. Nodes are 28 bytes (tag and content string offsets, attribute and
child counts, parent, first attribute, first child, reserved). Attributes are
pairs of string offsets. The child table lists node indices. Strings are
null-terminated UTF-8, addressed by offset into the string table.
"""

import struct
import xml.etree.ElementTree as ET

MAGIC = b"CryXmlB\x00"
_HEADER = struct.Struct("<9I")
_NODE = struct.Struct("<IIHHiIII")
_ATTRIBUTE = struct.Struct("<II")


def is_cryxml(data: bytes) -> bool:
    return data.startswith(MAGIC)


def decode(data: bytes) -> ET.Element:
    """Decode a CryXmlB document into an ElementTree element. Plain XML is parsed as is.

    Raises ValueError for a malformed document.
    """
    if not is_cryxml(data):
        return ET.fromstring(data)  # noqa: S314 - local game files; expat guards entity expansion
    try:
        return _decode(data)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise ValueError(f"malformed CryXmlB document ({e})") from e


def _decode(data: bytes) -> ET.Element:
    (
        _size,
        node_pos,
        node_count,
        attr_pos,
        _attr_count,
        child_pos,
        _child_count,
        string_pos,
        string_size,
    ) = _HEADER.unpack_from(data, len(MAGIC))
    tables = (
        (node_pos, node_count * _NODE.size),
        (attr_pos, _attr_count * _ATTRIBUTE.size),
        (child_pos, _child_count * 4),
        (string_pos, string_size),
    )
    if any(start + length > len(data) for start, length in tables):
        raise ValueError("CryXmlB table runs past the end of the data")
    strings = data[string_pos : string_pos + string_size]

    def text(offset: int) -> str:
        end = strings.index(b"\x00", offset)
        return strings[offset:end].decode("utf-8")

    nodes = [_NODE.unpack_from(data, node_pos + i * _NODE.size) for i in range(node_count)]
    elements: list[ET.Element] = []
    for tag, content, n_attrs, _n_children, _parent, first_attr, _first_child, _ in nodes:
        element = ET.Element(text(tag))
        for a in range(first_attr, first_attr + n_attrs):
            key, value = _ATTRIBUTE.unpack_from(data, attr_pos + a * _ATTRIBUTE.size)
            element.set(text(key), text(value))
        element.text = text(content) or None
        elements.append(element)

    adopted: set[int] = set()
    for element, (_, _, _, n_children, _, _, first_child, _) in zip(elements, nodes, strict=True):
        for c in range(first_child, first_child + n_children):
            (index,) = struct.unpack_from("<I", data, child_pos + c * 4)
            if index in adopted or elements[index] is element:
                raise ValueError("CryXmlB node has more than one parent, or is its own child")
            adopted.add(index)
            element.append(elements[index])
    roots = [e for e, node in zip(elements, nodes, strict=True) if node[4] < 0]
    if len(roots) != 1:
        raise ValueError(f"CryXmlB document has {len(roots)} root nodes; expected 1")
    return roots[0]
