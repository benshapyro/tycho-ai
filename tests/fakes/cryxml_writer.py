"""Encoding CryXmlB, so decoder tests don't need game files."""

import struct
import xml.etree.ElementTree as ET


def encode(root: ET.Element) -> bytes:
    nodes: list[ET.Element] = []
    parents: list[int] = []

    def walk(element: ET.Element, parent: int) -> None:
        index = len(nodes)
        nodes.append(element)
        parents.append(parent)
        for child in element:
            walk(child, index)

    walk(root, -1)
    strings = bytearray()
    offsets: dict[str, int] = {}

    def offset(text: str) -> int:
        if text not in offsets:
            offsets[text] = len(strings)
            strings.extend(text.encode() + b"\x00")
        return offsets[text]

    attributes: list[tuple[int, int]] = []
    children: list[int] = []
    node_records: list[bytes] = []
    for element in nodes:
        first_attr, first_child = len(attributes), len(children)
        attributes += [(offset(k), offset(v)) for k, v in element.attrib.items()]
        children += [nodes.index(c) for c in element]
        node_records.append(
            struct.pack(
                "<IIHHiIII",
                offset(element.tag),
                offset(element.text or ""),
                len(element.attrib),
                len(element),
                parents[nodes.index(element)],
                first_attr,
                first_child,
                0,
            )
        )
    node_table = b"".join(node_records)
    attr_table = b"".join(struct.pack("<II", k, v) for k, v in attributes)
    child_table = b"".join(struct.pack("<I", c) for c in children)

    header_size = 8 + 36
    node_pos = header_size
    attr_pos = node_pos + len(node_table)
    child_pos = attr_pos + len(attr_table)
    string_pos = child_pos + len(child_table)
    total = string_pos + len(strings)
    header = b"CryXmlB\x00" + struct.pack(
        "<9I",
        total,
        node_pos,
        len(nodes),
        attr_pos,
        len(attributes),
        child_pos,
        len(children),
        string_pos,
        len(strings),
    )
    return header + node_table + attr_table + child_table + bytes(strings)
