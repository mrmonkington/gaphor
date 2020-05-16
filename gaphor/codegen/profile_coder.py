"""Parse a SysML Gaphor Model and generate a SysML data model."""

from collections import deque
from os import PathLike
from typing import Deque, Dict, List, Optional, Set, TextIO, Tuple

from gaphor import UML
from gaphor.core.modeling import Element, ElementFactory
from gaphor.storage import storage
from gaphor.UML.modelinglanguage import UMLModelingLanguage

header = """# This file is generated by profile_coder.py. DO NOT EDIT!

from __future__ import annotations

from gaphor.core.modeling.properties import (
    association,
    attribute,
    enumeration,
    relation_many,
    relation_one,
)
"""


def type_converter(property: UML.Property) -> Optional[str]:
    """Convert association types for Python data model."""

    if property.type:
        return str(property.type.name)

    type_value = property.typeValue
    if type_value is None:
        raise ValueError(f"ERROR! type is not specified for property {property.name}")
    if type_value.lower() == "boolean":
        return "int"
    elif type_value.lower() in ("integer", "unlimitednatural"):
        return "int"
    elif type_value.lower() == "string" or type_value == "ValueSpecification":
        return "str"
    else:
        return str(type_value)


def filter_uml_classes(
    classes: List[UML.Class], modeling_language: UMLModelingLanguage
) -> List[UML.Class]:
    """Identify classes that are part of UML."""
    return [cls for cls in classes if modeling_language.lookup_element(cls.name)]


def find_enumerations(
    classes: List[UML.Class],
) -> Tuple[List[UML.Class], Dict[str, UML.Class]]:
    """Filter out enumerations."""
    enumerations = {cls.name: cls for cls in classes if "Kind" in cls.name}
    classes = [cls for cls in classes if "Kind" not in cls.name]
    return classes, enumerations


def get_class_extensions(cls: UML.Class):
    """Get the meta classes connected with extensions."""
    for a in cls.attribute["it.association"]:  # type: ignore
        if a.name == "baseClass":
            meta_cls = a.association.ownedEnd.class_
            yield meta_cls


def create_class_trees(classes: List[UML.Class]) -> Dict[UML.Class, List[UML.Class]]:
    """Create a tree of UML.Class elements.

    The relationship between the classes is a generalization. Since the opposite
    relationship, `cls.specific` is not currently stored, only the children
    know who their parents are, the parents don't know the children.

    """
    trees = {}
    for cls in classes:
        base_classes = [base_cls for base_cls in cls.general]
        meta_classes = [meta_cls for meta_cls in get_class_extensions(cls)]
        trees[cls] = base_classes + meta_classes
    return trees


def create_referenced(classes: List[UML.Class]) -> Set[UML.Class]:
    """UML.Class elements that are referenced by others.

    We consider a UML.Class referenced when its child UML.Class has a
    generalization relationship to it.

    """
    referenced = set()
    for cls in classes:
        for gen in cls.general:
            referenced.add(gen)
        for meta_cls in get_class_extensions(cls):
            referenced.add(meta_cls)
    return referenced


def write_class_def(cls, trees, f, cls_written=set()) -> None:
    if cls in cls_written:
        return

    generalizations = trees[cls]
    for g in generalizations:
        write_class_def(g, trees, f, cls_written)

    f.write(f"class {cls.name}({', '.join(g.name for g in generalizations)}):\n")
    write_attributes(cls, f)
    cls_written.add(cls)


def write_attributes(cls: UML.Class, f: TextIO) -> None:
    """Write attributes based on attribute type."""

    written = False
    for a in cls.attribute:  # type: ignore
        # TODO: do write derived values if override is available
        if not a.name or a.name == "baseClass" or a.isDerived:
            continue
        type_value = type_converter(a)
        if type_value:
            if type_value in ("int", "str"):
                f.write(f"    {a.name}: attribute[{type_value}]\n")
            elif "Kind" in type_value:
                f.write(f"    {a.name}: enumeration\n")
            elif a.upperValue == "1":
                f.write(f"    {a.name}: relation_one[{type_value}]\n")
            else:
                f.write(f"    {a.name}: relation_many[{type_value}]\n")
            written = True

    for o in cls.ownedOperation:
        f.write(f"    {o}: operation\n")
        written = True
    if not written:
        f.write("    pass\n\n")


def write_properties(
    cls: UML.Class, f: TextIO, enumerations: Dict[str, UML.Class]
) -> None:
    for a in cls.attribute:
        if not a.name or a.name == "baseClass" or a.isDerived:
            continue
        type_value = type_converter(a)
        if type_value in ("int", "str"):
            # TODO: add default value, if there is one
            f.write(f'{cls.name}.{a.name} = attribute("{a.name}", {type_value})\n')
        else:
            if not type_value:
                print(f"No type for {cls.name}.{a.name}")
                continue
            if "Kind" in type_value:
                enum = enumerations[type_value]
                values = tuple([a.name for a in enum.attribute])
                f.write(
                    f'{cls.name}.{a.name}.kind = enumeration("kind", {values}, "{values[0]}")\n'
                )
            else:
                lower = "" if a.lowerValue in (None, "0") else f", lower={a.lowerValue}"
                upper = (
                    "" if a.upperValue == "*" else f", upper=" f"{a.upperValue or 1}"
                )
                composite = ", composite=True" if a.aggregation == "composite" else ""
                opposite = (
                    f', opposite="{a.opposite.name}"'
                    if a.opposite and a.opposite.name and a.opposite.class_
                    else ""
                )

                f.write(
                    f'{cls.name}.{a.name} = association("{a.name}", {type_value}{lower}{upper}{composite}{opposite})\n'
                )


def generate(
    filename: PathLike, outfile: PathLike, overridesfile: Optional[PathLike] = None,
) -> None:
    """Generates the Python data model.

    Opens the Gaphor model, generates the list of classes using the element
    factory, and then creates a new Python data model using a relationship
    search tree.

    """
    element_factory = ElementFactory()
    modeling_language = UMLModelingLanguage()
    with open(filename):
        storage.load(
            filename, element_factory, modeling_language,
        )
    with open(outfile, "w") as f:
        f.write(header)
        classes: List = element_factory.lselect(lambda e: e.isKindOf(UML.Class))
        classes, enumerations = find_enumerations(classes)

        classes = sorted(
            (cls for cls in classes if cls.name[0] != "~"), key=lambda c: c.name
        )

        trees = create_class_trees(classes)
        create_referenced(classes)

        uml_classes = filter_uml_classes(classes, modeling_language)
        for cls in uml_classes:
            f.write(f"from gaphor.UML import {cls.name}\n")

        cls_written: Set[Element] = set(uml_classes)
        for cls in trees.keys():
            cls.attribute.sort(key=lambda a: a.name or "")  # type: ignore[attr-defined]
            write_class_def(cls, trees, f, cls_written)

        f.write("\n\n")

        for cls in trees.keys():
            write_properties(cls, f, enumerations)

    element_factory.shutdown()
