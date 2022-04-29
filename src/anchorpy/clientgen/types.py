from pathlib import Path
from typing import cast, Union as TypingUnion
from dataclasses import dataclass
from black import format_str, FileMode
from autoflake import fix_code
from pyheck import snake
from genpy import (
    FromImport,
    Import,
    Assign,
    Suite,
    Collection,
    ImportAs,
    Return,
    If,
    Raise,
    Generable,
)
from anchorpy.idl import (
    Idl,
    _IdlTypeDefTyStruct,
    _IdlField,
    _IdlType,
    _IdlEnumVariant,
    _IdlEnumFieldsTuple,
    _IdlEnumFieldsNamed,
)
from anchorpy.clientgen.genpy_extension import (
    Union,
    Class,
    Tuple,
    List,
    Method,
    InitMethod,
    ClassMethod,
    TypedParam,
    TypedDict,
    StrDict,
    StrDictEntry,
    Function,
    ANNOTATIONS_IMPORT,
)
from anchorpy.clientgen.common import (
    _fields_interface_name,
    _json_interface_name,
    _kind_interface_name,
    _value_interface_name,
    _py_type_from_idl,
    _idl_type_to_json_type,
    _struct_field_initializer,
    _layout_for_type,
    _field_from_decoded,
    _field_to_encodable,
    _field_to_json,
    _field_from_json,
)


def gen_types(idl: Idl, root: Path) -> None:
    types = idl.types
    if types is None or not types:
        return
    types_dir = root / "types"
    types_dir.mkdir(exist_ok=True)
    gen_index_file(idl, types_dir)
    gen_type_files(idl, types_dir)


def gen_index_file(idl: Idl, types_dir: Path) -> None:
    code = gen_index_code(idl)
    path = types_dir / "__init__.py"
    formatted = format_str(code, mode=FileMode())
    path.write_text(formatted)


def gen_index_code(idl: Idl) -> str:
    imports: list[TypingUnion[Import, FromImport]] = [Import("typing")]
    for ty in idl.types:
        ty_type = ty.type
        module_name = snake(ty.name)
        imports.append(FromImport(".", [module_name]))
        if isinstance(ty_type, _IdlTypeDefTyStruct):
            import_members = [
                ty.name,
                _fields_interface_name(ty.name),
                _json_interface_name(ty.name),
            ]
        else:
            import_members = [
                _kind_interface_name(ty.name),
                _json_interface_name(ty.name),
            ]
        imports.append(
            FromImport(
                f".{module_name}",
                import_members,
            )
        )
    return str(Collection(imports))


def gen_type_files(idl: Idl, types_dir: Path) -> None:
    types_code = gen_types_code(idl, types_dir)
    for path, code in types_code.items():
        formatted = format_str(code, mode=FileMode())
        fixed = fix_code(formatted, remove_all_unused_imports=True)
        path.write_text(fixed)


def gen_types_code(idl: Idl, out: Path) -> dict[Path, str]:
    res = {}
    types_module_names = [snake(ty.name) for ty in idl.types]
    for ty in idl.types:
        module_name = snake(ty.name)
        relative_import = FromImport(
            ".", [mod for mod in types_module_names if mod != module_name]
        )
        body = (
            gen_struct(idl, ty.name, ty.type.fields)
            if isinstance(ty.type, _IdlTypeDefTyStruct)
            else gen_enum(idl, ty.name, ty.type.variants)
        )
        code = str(Collection([relative_import, body]))
        path = (out / module_name).with_suffix(".py")
        res[path] = code
    return res


def gen_struct(idl: Idl, name: str, fields: list[_IdlField]) -> Collection:
    imports = [
        Import("typing"),
        FromImport("dataclasses", ["dataclass"]),
        FromImport("construct", ["Container"]),
        FromImport("solana.publickey", ["PublicKey"]),
        ImportAs("borsh_construct", "borsh"),
    ]
    fields_interface_name = _fields_interface_name(name)
    json_interface_name = _json_interface_name(name)
    fields_interface_params: list[TypedParam] = []
    json_interface_params: list[TypedParam] = []
    layout_items: list[str] = []
    from_decoded_items: list[str] = []
    to_encodable_items: list[str] = []
    to_json_items: list[str] = []
    from_json_items: list[str] = []
    for field in fields:
        fields_interface_params.append(
            TypedParam(
                field.name,
                _py_type_from_idl(idl=idl, ty=field.type, types_relative_imports=True),
            )
        )
        json_interface_params.append(
            TypedParam(
                field.name,
                _idl_type_to_json_type(ty=field.type, types_relative_imports=True),
            )
        )
        layout_items.append(
            _layout_for_type(
                idl=idl, ty=field.type, name=field.name, types_relative_imports=True
            )
        )
        from_decoded_item_val = _field_from_decoded(
            idl=idl, ty=field, val_prefix="obj.", types_relative_imports=True
        )
        from_decoded_items.append(f"{field.name}={from_decoded_item_val}")
        as_encodable = _field_to_encodable(
            idl=idl,
            ty=field,
            val_prefix='fields["',
            val_suffix='"]',
            types_relative_imports=True,
        )
        to_encodable_items.append(f'"{field.name}": {as_encodable}')
        to_json_items.append(f'"{field.name}": {_field_to_json(idl, field, "self.")}')
        field_from_json = _field_from_json(
            idl=idl, ty=field, types_relative_imports=True
        )
        from_json_items.append(f"{field.name}={field_from_json}")
    fields_interface = TypedDict(fields_interface_name, fields_interface_params)
    json_interface = TypedDict(json_interface_name, json_interface_params)
    layout = f"borsh.CStruct({','.join(layout_items)})"
    args_for_from_decoded = ",".join(from_decoded_items)
    to_encodable_body = "{" + ",".join(to_encodable_items) + "}"
    to_json_body = "{" + ",".join(to_json_items) + "}"
    args_for_from_json = ",".join(from_json_items)
    struct_cls = Class(
        name,
        None,
        [
            Assign("layout", layout),
            InitMethod(
                [TypedParam("fields", fields_interface_name)],
                Suite(
                    [
                        Assign(
                            f"self.{field.name}",
                            _struct_field_initializer(
                                idl=idl, field=field, types_relative_imports=True
                            ),
                        )
                        for field in fields
                    ]
                ),
            ),
            ClassMethod(
                "from_decoded",
                [TypedParam("obj", "Container")],
                Return(f"cls({fields_interface_name}({args_for_from_decoded}))"),
                f'"{name}"',
            ),
            ClassMethod(
                "to_encodable",
                [TypedParam("fields", fields_interface_name)],
                Return(to_encodable_body),
                "dict[str, typing.Any]",
            ),
            Method("to_json", [], Return(to_json_body), json_interface_name),
            ClassMethod(
                "from_json",
                [TypedParam("obj", json_interface_name)],
                Return(f"cls({json_interface_name}({args_for_from_json}))"),
                f'"{name}"',
            ),
        ],
    )
    return Collection([*imports, fields_interface, json_interface, struct_cls])


def _make_cstruct(fields: dict[str, str]) -> str:
    formatted_fields = ",".join([f'"{key}" / {val}' for key, val in fields.items()])
    return f"borsh.CStruct({formatted_fields})"


@dataclass
class _NamedFieldRecord:
    field_type_alias_entry: TypedParam
    value_type_alias_entry: TypedParam
    json_interface_value_type_entry: TypedParam
    init_method_body_item: StrDictEntry
    json_value_item: StrDictEntry
    encodable_value_item: StrDictEntry
    init_entry_for_from_decoded: StrDictEntry
    init_entry_for_from_json: StrDictEntry


def _make_named_field_record(named_field: _IdlField, idl: Idl) -> _NamedFieldRecord:
    return _NamedFieldRecord(
        field_type_alias_entry=TypedParam(
            named_field.name,
            _py_type_from_idl(
                idl=idl, ty=named_field.type, types_relative_imports=True
            ),
        ),
        value_type_alias_entry=TypedParam(
            named_field.name,
            _py_type_from_idl(
                idl=idl,
                ty=named_field.type,
                types_relative_imports=True,
                use_fields_interface_for_struct=False,
            ),
        ),
        json_interface_value_type_entry=TypedParam(
            named_field.name,
            _idl_type_to_json_type(ty=named_field.type, types_relative_imports=True),
        ),
        init_method_body_item=StrDictEntry(
            named_field.name,
            _struct_field_initializer(
                idl=idl,
                field=named_field,
                prefix='value["',
                types_relative_imports=True,
            ),
        ),
        json_value_item=StrDictEntry(
            named_field.name,
            _field_to_json(idl, named_field, 'self.value["', val_suffix='"]'),
        ),
        encodable_value_item=StrDictEntry(
            named_field.name,
            _field_to_encodable(
                idl=idl,
                ty=named_field,
                val_prefix='self.value["',
                val_suffix='"]',
                types_relative_imports=True,
            ),
        ),
        init_entry_for_from_decoded=StrDictEntry(
            named_field.name,
            _field_from_decoded(
                idl=idl,
                ty=_IdlField(f'val["{named_field.name}"]', named_field.type),
                val_prefix="",
            ),
        ),
        init_entry_for_from_json=StrDictEntry(
            named_field.name,
            _field_from_json(
                idl=idl,
                ty=named_field,
                json_param_name='obj["value"]',
                types_relative_imports=True,
            ),
        ),
    )


@dataclass
class _UnnamedFieldRecord:
    field_type_alias_element: str
    value_type_alias_element: str
    json_interface_value_element: str
    tuple_element: str
    json_value_element: str
    encodable_value_item: StrDict
    init_element_for_from_decoded: str
    init_element_for_from_json: str


def _make_unnamed_field_record(
    index: int,
    unnamed_field: _IdlType,
    idl: Idl,
) -> _UnnamedFieldRecord:
    elem_name = f"value[{index}]"
    encodable = _field_to_encodable(
        idl=idl,
        ty=_IdlField(f"[{index}]", unnamed_field),
        val_prefix="self.value",
        types_relative_imports=True,
    )
    return _UnnamedFieldRecord(
        field_type_alias_element=_py_type_from_idl(
            idl=idl,
            ty=unnamed_field,
            types_relative_imports=True,
        ),
        value_type_alias_element=_py_type_from_idl(
            idl=idl,
            ty=unnamed_field,
            types_relative_imports=True,
            use_fields_interface_for_struct=False,
        ),
        json_interface_value_element=_idl_type_to_json_type(
            ty=unnamed_field, types_relative_imports=True
        ),
        tuple_element=_struct_field_initializer(
            idl=idl,
            field=_IdlField(elem_name, unnamed_field),
            prefix="",
            suffix="",
            types_relative_imports=True,
        ),
        json_value_element=_field_to_json(idl, _IdlField(elem_name, unnamed_field)),
        encodable_value_item=StrDictEntry(f"_{index}", encodable),
        init_element_for_from_decoded=_field_from_decoded(
            idl=idl,
            ty=_IdlField(f'val["_{index}"]', unnamed_field),
            val_prefix="",
            types_relative_imports=True,
        ),
        init_element_for_from_json=_field_from_json(
            idl=idl,
            ty=_IdlField(f"value[{index}]", unnamed_field),
            types_relative_imports=True,
        ),
    )


def gen_enum(idl: Idl, name: str, variants: list[_IdlEnumVariant]) -> Collection:
    imports = [
        ANNOTATIONS_IMPORT,
        Import("typing"),
        FromImport("solana.publickey", ["PublicKey"]),
        FromImport("anchorpy.borsh_extension", ["EnumForCodegen", "BorshPubkey"]),
        ImportAs("borsh_construct", "borsh"),
    ]
    invalid_enum_raise = Raise('ValueError("Invalid enum object")')
    from_decoded_dict_check = If("not isinstance(obj, dict)", invalid_enum_raise)
    variant_name_in_obj_checks: list[Generable] = []
    obj_kind_checks: list[Generable] = []
    json_interfaces: list[TypedDict] = []
    classes: list[Class] = []
    cstructs: list[str] = []
    type_variants_members: list[str] = []
    json_variants_members: list[str] = []
    for idx, variant in enumerate(variants):
        discriminator = idx
        fields = variant.fields
        fields_interface_name = _fields_interface_name(variant.name)
        value_interface_name = _value_interface_name(variant.name)
        json_interface_name = _json_interface_name(variant.name)
        json_interface_value_type_name = f"{json_interface_name}Value"
        fields_type_aliases: list[TypingUnion[TypedDict, Assign]] = []
        value_type_aliases: list[TypingUnion[TypedDict, Assign]] = []
        extra_aliases: list[TypedDict] = []
        encodable_value_items: list[StrDictEntry] = []
        cstruct_fields: dict[str, str] = {}
        to_json_items_base = StrDictEntry("kind", f'"{variant.name}"')
        json_interface_kind_field = TypedParam(
            "kind", f'typing.Literal["{variant.name}"]'
        )
        type_variants_members.append(variant.name)
        json_variants_members.append(_json_interface_name(variant.name))

        def make_variant_name_in_obj_check(then: Generable) -> Generable:
            return If(f'"{variant.name}" in obj', then)

        def make_obj_kind_check(return_val: str) -> Generable:
            lhs = 'obj["kind"] == '
            return If(f'{lhs}"{variant.name}"', Return(return_val))

        if fields:
            val_line_for_from_decoded = Assign("val", f'obj["{variant.name}"]')
            if isinstance(fields[0], _IdlField):
                named_enum_fields = cast(_IdlEnumFieldsNamed, fields)
                field_type_alias_entries: list[TypedParam] = []
                value_type_alias_entries: list[TypedParam] = []
                json_interface_value_type_entries: list[TypedParam] = []
                init_method_body_items: list[StrDictEntry] = []
                json_value_items: list[StrDictEntry] = []
                init_entries_for_from_decoded: list[StrDictEntry] = []
                init_entries_for_from_json: list[StrDictEntry] = []
                for named_field in named_enum_fields:
                    rec = _make_named_field_record(named_field, idl)
                    field_type_alias_entries.append(rec.field_type_alias_entry)
                    value_type_alias_entries.append(rec.value_type_alias_entry)
                    json_interface_value_type_entries.append(
                        rec.json_interface_value_type_entry
                    )
                    init_method_body_items.append(rec.init_method_body_item)
                    json_value_items.append(rec.json_value_item)
                    encodable_value_items.append(rec.encodable_value_item)
                    init_entries_for_from_decoded.append(
                        rec.init_entry_for_from_decoded
                    )
                    init_entries_for_from_json.append(rec.init_entry_for_from_json)

                    cstruct_fields[named_field.name] = _layout_for_type(
                        idl=idl, ty=named_field.type, types_relative_imports=True
                    )

                fields_type_aliases.append(
                    TypedDict(fields_interface_name, field_type_alias_entries)
                )
                value_type_aliases.append(
                    TypedDict(value_interface_name, value_type_alias_entries)
                )
                json_interface_value_field_type = TypedDict(
                    json_interface_value_type_name, json_interface_value_type_entries
                )
                extra_aliases.append(json_interface_value_field_type)
                init_method_body = Assign(
                    "self.value",
                    StrDict(init_method_body_items),
                )
                json_value_entry = StrDict(json_value_items)
                to_json_body = Return(
                    StrDict(
                        [to_json_items_base, StrDictEntry("value", json_value_entry)]
                    )
                )
                init_arg_for_from_decoded = StrDict(init_entries_for_from_decoded)
                init_arg_for_from_json = StrDict(init_entries_for_from_json)
            else:
                tuple_enum_fields = cast(_IdlEnumFieldsTuple, fields)
                field_type_alias_elements: list[str] = []
                value_type_alias_elements: list[str] = []
                json_interface_value_elements: list[str] = []
                tuple_elements: list[str] = []
                json_value_elements: list[str] = []
                init_elements_for_from_decoded: list[str] = []
                init_elements_for_from_json: list[str] = []
                for i, unnamed_field in enumerate(tuple_enum_fields):
                    rec_unnamed = _make_unnamed_field_record(i, unnamed_field, idl)
                    field_type_alias_elements.append(
                        rec_unnamed.field_type_alias_element
                    )
                    value_type_alias_elements.append(
                        rec_unnamed.value_type_alias_element
                    )
                    json_interface_value_elements.append(
                        rec_unnamed.json_interface_value_element
                    )
                    tuple_elements.append(rec_unnamed.tuple_element)
                    json_value_elements.append(rec_unnamed.json_value_element)
                    encodable_value_items.append(rec_unnamed.encodable_value_item)
                    init_elements_for_from_decoded.append(
                        rec_unnamed.init_element_for_from_decoded
                    )
                    init_elements_for_from_json.append(
                        rec_unnamed.init_element_for_from_json
                    )
                    cstruct_fields[f"_{i}"] = _layout_for_type(
                        idl=idl, ty=unnamed_field, types_relative_imports=True
                    )
                fields_type_aliases.append(
                    Assign(
                        fields_interface_name,
                        Tuple(field_type_alias_elements),
                    )
                )
                value_type_aliases.append(
                    Assign(
                        value_interface_name,
                        Tuple(value_type_alias_elements),
                    )
                )
                json_interface_value_field_type = Tuple(json_interface_value_elements)
                init_method_body = Assign(
                    "self.value",
                    Tuple(tuple_elements),
                )
                to_json_body = Return(
                    StrDict(
                        [
                            to_json_items_base,
                            StrDictEntry("value", str(List(json_value_elements))),
                        ]
                    )
                )
                init_arg_for_from_decoded = Tuple(init_elements_for_from_decoded)
                init_arg_for_from_json = Tuple(init_elements_for_from_json)
            init_method = InitMethod(
                [TypedParam("value", fields_interface_name)], init_method_body
            )
            init_method_container = [init_method]
            to_json_method = Method("to_json", [], to_json_body, json_interface_name)
            encodable_value_entry = StrDict(encodable_value_items)
            to_encodable_body = Return(
                StrDict([StrDictEntry(variant.name, encodable_value_entry)])
            )
            to_encodable_method = Method("to_encodable", [], to_encodable_body, "dict")
            json_interface_params = [
                TypedParam("value", json_interface_value_type_name),
                json_interface_kind_field,
            ]
            variant_name_in_obj_check = make_variant_name_in_obj_check(
                Suite(
                    [
                        val_line_for_from_decoded,
                        Return(f"{variant.name}({init_arg_for_from_decoded})"),
                    ]
                )
            )
            obj_kind_check = make_obj_kind_check(
                f"{variant.name}({init_arg_for_from_json})",
            )
        else:
            to_json_method = ClassMethod(
                "to_json",
                [],
                Return(StrDict([to_json_items_base])),
                json_interface_name,
            )
            to_encodable_method = ClassMethod(
                "to_encodable",
                [],
                Return(StrDict([StrDictEntry(variant.name, StrDict([]))])),
                "dict",
            )
            init_method_container = []
            json_interface_params = [json_interface_kind_field]
            variant_name_in_obj_check = make_variant_name_in_obj_check(
                Return(f"{variant.name}()")
            )
            obj_kind_check = make_obj_kind_check(f"{variant.name}()")
        json_interfaces.append(TypedDict(json_interface_name, json_interface_params))
        class_common_attrs = [
            Assign("discriminator", discriminator),
            Assign("kind", f'"{variant.name}"'),
        ]

        attrs = [
            *class_common_attrs,
            *init_method_container,
            to_json_method,
            to_encodable_method,
        ]
        classes.append(Class(variant.name, None, attrs))
        variant_name_in_obj_checks.append(variant_name_in_obj_check)
        obj_kind_checks.append(obj_kind_check)
        cstructs.append(f'"{variant.name}" / {_make_cstruct(cstruct_fields)}')
    from_decoded_fn = Function(
        "from_decoded",
        [TypedParam("obj", "dict")],
        Suite(
            [from_decoded_dict_check, *variant_name_in_obj_checks, invalid_enum_raise]
        ),
        _kind_interface_name(name),
    )
    from_json_fn = Function(
        "from_json",
        [TypedParam("obj", _json_interface_name(name))],
        Suite(
            [
                *obj_kind_checks,
                Assign("kind", 'obj["kind"]'),
                Raise('ValueError(f"Uncrecognized enum kind: {kind}")'),
            ]
        ),
        _kind_interface_name(name),
    )
    formatted_cstructs = ",".join(cstructs)
    layout_assignment = Assign(
        "layout",
        f"EnumForCodegen({formatted_cstructs})",
    )
    json_variants = Union(json_variants_members)
    type_variants = Union(type_variants_members)
    kind_type_alias = Assign(_kind_interface_name(name), type_variants)
    json_type_alias = Assign(_json_interface_name(name), json_variants)
    return Collection(
        [
            *imports,
            *extra_aliases,
            *fields_type_aliases,
            *value_type_aliases,
            *json_interfaces,
            *classes,
            kind_type_alias,
            json_type_alias,
            from_decoded_fn,
            from_json_fn,
            layout_assignment,
        ]
    )
