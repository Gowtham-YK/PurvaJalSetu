import ast
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ============================================================
# CONFIG
# ============================================================

ROOT = Path(__file__).resolve().parent

APP_FILE = ROOT / "app.py"
TEMPLATES_DIR = ROOT / "templates"

CSV_DIRECTORIES = [
    ROOT / "database",
    ROOT / "data",
]


errors = []
warnings = []
successes = []


# ============================================================
# OUTPUT HELPERS
# ============================================================

def add_error(message):
    errors.append(message)


def add_warning(message):
    warnings.append(message)


def add_success(message):
    successes.append(message)


# ============================================================
# 1. CHECK APP.PY EXISTS
# ============================================================

print()
print("=" * 70)
print("JALSETU PROJECT CHECKER")
print("=" * 70)
print()


if not APP_FILE.exists():

    print("ERROR: app.py was not found.")
    sys.exit(1)


# ============================================================
# 2. PYTHON SYNTAX CHECK
# ============================================================

try:

    source = APP_FILE.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source,
        filename=str(APP_FILE)
    )

    add_success(
        "app.py Python syntax is valid."
    )

except SyntaxError as e:

    add_error(
        f"Python syntax error in app.py "
        f"at line {e.lineno}: {e.msg}"
    )

    tree = None
    source = ""


# ============================================================
# 3. COLLECT FLASK ROUTES
# ============================================================

routes = []
endpoints = set()


if tree is not None:

    for node in ast.walk(tree):

        if not isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef
            )
        ):
            continue

        for decorator in node.decorator_list:

            if not isinstance(
                decorator,
                ast.Call
            ):
                continue

            func = decorator.func

            if not (
                isinstance(func, ast.Attribute)
                and
                func.attr == "route"
            ):
                continue

            route_path = None

            if (
                decorator.args
                and
                isinstance(
                    decorator.args[0],
                    ast.Constant
                )
            ):

                route_path = (
                    decorator.args[0].value
                )

            methods = []

            for keyword in decorator.keywords:

                if keyword.arg != "methods":
                    continue

                if isinstance(
                    keyword.value,
                    (
                        ast.List,
                        ast.Tuple
                    )
                ):

                    for element in keyword.value.elts:

                        if isinstance(
                            element,
                            ast.Constant
                        ):
                            methods.append(
                                str(
                                    element.value
                                ).upper()
                            )

            if not methods:

                methods = ["GET"]

            endpoints.add(
                node.name
            )

            routes.append({
                "endpoint": node.name,
                "path": route_path,
                "methods": tuple(
                    sorted(methods)
                ),
                "line": node.lineno,
            })


add_success(
    f"Found {len(routes)} Flask route definitions."
)


# ============================================================
# 4. DUPLICATE ENDPOINT NAMES
# ============================================================

endpoint_counter = Counter(
    route["endpoint"]
    for route in routes
)


for endpoint, count in endpoint_counter.items():

    if count > 1:

        matching = [
            route
            for route in routes
            if route["endpoint"] == endpoint
        ]

        locations = ", ".join(
            f"line {route['line']}"
            for route in matching
        )

        add_error(
            f"Duplicate Flask endpoint "
            f"'{endpoint}' found at {locations}."
        )


# ============================================================
# 5. DUPLICATE ROUTE + METHOD COMBINATIONS
# ============================================================

route_map = defaultdict(list)


for route in routes:

    key = (
        route["path"],
        route["methods"]
    )

    route_map[key].append(
        route
    )


for key, matching in route_map.items():

    if len(matching) <= 1:
        continue

    path, methods = key

    endpoints_text = ", ".join(
        (
            f"{route['endpoint']} "
            f"(line {route['line']})"
        )
        for route in matching
    )

    add_error(
        f"Duplicate route {path} "
        f"{'/'.join(methods)} -> "
        f"{endpoints_text}"
    )


# ============================================================
# 6. CHECK TEMPLATE FILES
# ============================================================

if not TEMPLATES_DIR.exists():

    add_error(
        "templates/ directory does not exist."
    )

else:

    template_files = list(
        TEMPLATES_DIR.glob("*.html")
    )

    add_success(
        f"Found {len(template_files)} HTML templates."
    )


# ============================================================
# 7. CHECK render_template() FILES EXIST
# ============================================================

if tree is not None:

    for node in ast.walk(tree):

        if not isinstance(
            node,
            ast.Call
        ):
            continue

        if not (
            isinstance(node.func, ast.Name)
            and
            node.func.id == "render_template"
        ):
            continue

        if not node.args:
            continue

        first_arg = node.args[0]

        if not isinstance(
            first_arg,
            ast.Constant
        ):
            continue

        template_name = first_arg.value

        if not isinstance(
            template_name,
            str
        ):
            continue

        template_path = (
            TEMPLATES_DIR
            /
            template_name
        )

        if not template_path.exists():

            add_error(
                f"app.py line {node.lineno}: "
                f"render_template('{template_name}') "
                f"but templates/{template_name} "
                f"does not exist."
            )


# ============================================================
# 8. CHECK url_for() REFERENCES IN TEMPLATES
# ============================================================

template_url_refs = []


if TEMPLATES_DIR.exists():

    for template_path in (
        TEMPLATES_DIR.rglob("*.html")
    ):

        try:

            template_source = (
                template_path.read_text(
                    encoding="utf-8",
                    errors="ignore"
                )
            )

        except Exception as e:

            add_warning(
                f"Could not read "
                f"{template_path.name}: {e}"
            )

            continue

        pattern = re.compile(
            r"""url_for\(\s*['"]([^'"]+)['"]"""
        )

        for match in pattern.finditer(
            template_source
        ):

            endpoint = match.group(1)

            line_number = (
                template_source[
                    :match.start()
                ].count("\n")
                + 1
            )

            template_url_refs.append({
                "endpoint": endpoint,
                "file": template_path,
                "line": line_number,
            })


for ref in template_url_refs:

    endpoint = ref["endpoint"]

    if endpoint == "static":
        continue

    if endpoint not in endpoints:

        relative_file = ref[
            "file"
        ].relative_to(ROOT)

        add_error(
            f"{relative_file} line "
            f"{ref['line']}: "
            f"url_for('{endpoint}') "
            f"references a Flask endpoint "
            f"that does not exist."
        )


# ============================================================
# 9. CHECK CSV STRUCTURE
# ============================================================

csv_count = 0


for directory in CSV_DIRECTORIES:

    if not directory.exists():
        continue

    for csv_path in directory.rglob("*.csv"):

        csv_count += 1

        try:

            with open(
                csv_path,
                "r",
                newline="",
                encoding="utf-8-sig"
            ) as file:

                reader = csv.reader(file)

                rows = list(reader)

            if not rows:

                add_warning(
                    f"{csv_path.relative_to(ROOT)} "
                    f"is empty."
                )

                continue

            expected_columns = len(
                rows[0]
            )

            for index, row in enumerate(
                rows[1:],
                start=2
            ):

                if len(row) != expected_columns:

                    add_error(
                        f"{csv_path.relative_to(ROOT)} "
                        f"line {index}: "
                        f"expected "
                        f"{expected_columns} columns "
                        f"but found {len(row)}."
                    )

        except Exception as e:

            add_error(
                f"Could not parse "
                f"{csv_path.relative_to(ROOT)}: "
                f"{e}"
            )


add_success(
    f"Checked {csv_count} CSV files."
)


# ============================================================
# 10. SPECIAL TANKER REGISTRATION CSV CHECK
# ============================================================

tanker_csv = (
    ROOT
    /
    "database"
    /
    "tanker_registrations.csv"
)


expected_tanker_fields = [
    "operator_id",
    "operator_name",
    "operator_type",
    "phone",
    "email",
    "area",
    "pincode",
    "latitude",
    "longitude",
    "operational_tankers",
    "contract_id",
    "contract_start",
    "contract_end",
    "tanker_registration_no",
    "tanker_capacity_kl",
    "vehicle_model",
    "water_type_supported",
    "service_radius_km",
    "verification_status",
    "registration_date",
]


if tanker_csv.exists():

    try:

        with open(
            tanker_csv,
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as file:

            reader = csv.DictReader(file)

            actual_fields = (
                reader.fieldnames
                or []
            )

            if actual_fields != expected_tanker_fields:

                add_error(
                    "tanker_registrations.csv "
                    "header does not match the "
                    "expected 20-column schema."
                )

            operator_ids = []

            for row in reader:

                operator_id = str(
                    row.get(
                        "operator_id"
                    ) or ""
                ).strip()

                if operator_id:

                    operator_ids.append(
                        operator_id
                    )

            duplicate_ids = [
                operator_id
                for operator_id, count
                in Counter(
                    operator_ids
                ).items()
                if count > 1
            ]

            if duplicate_ids:

                add_error(
                    "Duplicate tanker operator IDs: "
                    +
                    ", ".join(
                        duplicate_ids
                    )
                )

    except Exception as e:

        add_error(
            "Unable to validate "
            "tanker_registrations.csv: "
            f"{e}"
        )


# ============================================================
# FINAL REPORT
# ============================================================

print()
print("=" * 70)
print("SUCCESSFUL CHECKS")
print("=" * 70)

for message in successes:
    print("[OK]", message)


print()
print("=" * 70)
print("WARNINGS")
print("=" * 70)

if warnings:

    for message in warnings:
        print("[WARNING]", message)

else:
    print("No warnings.")


print()
print("=" * 70)
print("ERRORS")
print("=" * 70)

if errors:

    for number, message in enumerate(
        errors,
        start=1
    ):

        print(
            f"[ERROR {number}]",
            message
        )

else:

    print("No static project errors detected.")


print()
print("=" * 70)

if errors:

    print(
        f"RESULT: FAILED — "
        f"{len(errors)} error(s), "
        f"{len(warnings)} warning(s)"
    )

    print("=" * 70)
    print()

    sys.exit(1)

else:

    print(
        f"RESULT: PASSED — "
        f"{len(warnings)} warning(s)"
    )

    print("=" * 70)
    print()

    sys.exit(0)