# RenderDoc2obj
# usage:    python RenderDoc2obj.py

import argparse
import csv
import os
import sys

VS_INPUT_EXPORT = 'vs_input'
VS_OUTPUT_EXPORT = 'vs_output'


def detect_export_type(fieldnames):
    stripped_to_original = {
        fieldname.strip(): fieldname
        for fieldname in fieldnames
        if fieldname is not None
    }
    normalized_fieldnames = [fieldname.strip() for fieldname in fieldnames if fieldname is not None]

    vs_output_candidates = [
        ('SV_POSITION.x', 'SV_POSITION.y', 'SV_POSITION.z', 'SV_POSITION.w'),
        ('SV_Position.x', 'SV_Position.y', 'SV_Position.z', 'SV_Position.w'),
    ]
    for columns in vs_output_candidates:
        if all(column in stripped_to_original for column in columns):
            return VS_OUTPUT_EXPORT, tuple(stripped_to_original[column] for column in columns), None

    vs_input_columns = ('ATTRIBUTE0.x', 'ATTRIBUTE0.y', 'ATTRIBUTE0.z')
    if all(column in stripped_to_original for column in vs_input_columns):
        return VS_INPUT_EXPORT, tuple(stripped_to_original[column] for column in vs_input_columns), None

    if len(normalized_fieldnames) >= 5 and normalized_fieldnames[0] == 'VTX' and normalized_fieldnames[1] == 'IDX':
        fallback_columns = tuple(fieldnames[2:5])
        return VS_INPUT_EXPORT, fallback_columns, (
            f"Info: Falling back to VS Input-style position columns {fallback_columns}."
        )

    if len(fieldnames) >= 3:
        fallback_columns = tuple(fieldnames[:3])
        return VS_INPUT_EXPORT, fallback_columns, (
            f"Info: Falling back to raw position columns {fallback_columns}."
        )

    return None, None, None


def get_export_label(export_type):
    if export_type == VS_OUTPUT_EXPORT:
        return 'VS Output'
    if export_type == VS_INPUT_EXPORT:
        return 'VS Input'
    return 'Unknown'


def find_vs_input_uv_columns(fieldnames, position_columns):
    attribute_components = {}
    attribute_order = []
    position_bases = {
        column.strip().rsplit('.', 1)[0]
        for column in position_columns
        if column is not None and column.strip().startswith('ATTRIBUTE') and '.' in column.strip()
    }

    for fieldname in fieldnames:
        if fieldname is None:
            continue

        stripped_fieldname = fieldname.strip()
        if not stripped_fieldname.startswith('ATTRIBUTE') or '.' not in stripped_fieldname:
            continue

        attribute_base, attribute_component = stripped_fieldname.rsplit('.', 1)
        if attribute_component not in {'x', 'y', 'z', 'w'}:
            continue

        if attribute_base not in attribute_components:
            attribute_components[attribute_base] = {}
            attribute_order.append(attribute_base)
        attribute_components[attribute_base][attribute_component] = fieldname

    for attribute_base in attribute_order:
        if attribute_base in position_bases:
            continue

        components = attribute_components[attribute_base]
        if 'x' in components and 'y' in components and 'z' not in components and 'w' not in components:
            return (components['x'], components['y'])

    return None


def find_vs_output_uv_columns(fieldnames):
    texcoord_components = {}
    texcoord_order = []

    for fieldname in fieldnames:
        if fieldname is None:
            continue

        stripped_fieldname = fieldname.strip()
        if not stripped_fieldname.startswith('TEXCOORD') or '.' not in stripped_fieldname:
            continue

        texcoord_base, texcoord_component = stripped_fieldname.rsplit('.', 1)
        if texcoord_component not in {'x', 'y', 'z', 'w'}:
            continue

        if texcoord_base not in texcoord_components:
            texcoord_components[texcoord_base] = {}
            texcoord_order.append(texcoord_base)
        texcoord_components[texcoord_base][texcoord_component] = fieldname

    for texcoord_base in texcoord_order:
        components = texcoord_components[texcoord_base]
        if 'x' in components and 'y' in components:
            return (components['x'], components['y'])

    return None


def parse_csv_vector(raw_value, expected_size, field_name):
    values = [float(value.strip()) for value in raw_value.split(',')]
    if len(values) != expected_size:
        raise ValueError(f"Could not read {expected_size} values for {field_name}")
    return values


def load_view_export_rows(matrix_file):
    with open(matrix_file, 'r', newline='') as infile:
        reader = csv.DictReader(infile)
        required_columns = {'Name', 'Value'}
        if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
            raise ValueError(
                f"{matrix_file} must be a RenderDoc constant-buffer CSV export with Name and Value columns"
            )

        rows_by_name = {}
        for row in reader:
            row_name = (row.get('Name') or '').strip()
            if not row_name:
                continue
            rows_by_name[row_name] = (row.get('Value') or '').strip()
        return rows_by_name


def load_view_proj_matrix(matrix_file):
    rows_by_name = load_view_export_rows(matrix_file)
    matrix_rows = []
    for row_index in range(4):
        row_name = f'TranslatedWorldToClip.row{row_index}'
        row_value = rows_by_name.get(row_name)
        if not row_value:
            raise ValueError(f"Could not find {row_name} in {matrix_file}")
        matrix_rows.append(parse_csv_vector(row_value, 4, row_name))
    return matrix_rows


def load_view_origin(matrix_file):
    rows_by_name = load_view_export_rows(matrix_file)
    view_origin_high = None
    view_origin_low = None
    world_camera_origin = None
    pre_view_translation = None

    row_value = rows_by_name.get('ViewOriginHigh')
    if row_value:
        view_origin_high = parse_csv_vector(row_value, 3, 'ViewOriginHigh')

    row_value = rows_by_name.get('ViewOriginLow')
    if row_value:
        view_origin_low = parse_csv_vector(row_value, 3, 'ViewOriginLow')

    row_value = rows_by_name.get('WorldCameraOrigin')
    if row_value:
        world_camera_origin = parse_csv_vector(row_value, 3, 'WorldCameraOrigin')

    row_value = rows_by_name.get('PreViewTranslation')
    if row_value:
        pre_view_translation = parse_csv_vector(row_value, 3, 'PreViewTranslation')

    if view_origin_high is not None:
        if view_origin_low is None:
            view_origin_low = [0.0, 0.0, 0.0]
        return [high + low for high, low in zip(view_origin_high, view_origin_low)]

    if world_camera_origin is not None:
        return world_camera_origin

    if pre_view_translation is not None:
        return [-value for value in pre_view_translation]

    return None


def invert_matrix(matrix):
    size = len(matrix)
    augmented = [row[:] + identity_row for row, identity_row in zip(matrix, identity_matrix(size))]

    for pivot_index in range(size):
        pivot_row = max(range(pivot_index, size), key=lambda row_index: abs(augmented[row_index][pivot_index]))
        pivot_value = augmented[pivot_row][pivot_index]
        if abs(pivot_value) < 1e-12:
            raise ValueError('Matrix not invertable')

        if pivot_row != pivot_index:
            augmented[pivot_index], augmented[pivot_row] = augmented[pivot_row], augmented[pivot_index]

        pivot_value = augmented[pivot_index][pivot_index]
        augmented[pivot_index] = [value / pivot_value for value in augmented[pivot_index]]

        for row_index in range(size):
            if row_index == pivot_index:
                continue
            factor = augmented[row_index][pivot_index]
            augmented[row_index] = [
                current - factor * pivot
                for current, pivot in zip(augmented[row_index], augmented[pivot_index])
            ]

    return [row[size:] for row in augmented]


def identity_matrix(size):
    return [[1.0 if row == col else 0.0 for col in range(size)] for row in range(size)]


def multiply_vector_matrix(vector, matrix):
    column_count = len(matrix[0])
    return [
        sum(vector[row_index] * matrix[row_index][column_index] for row_index in range(len(vector)))
        for column_index in range(column_count)
    ]


def get_vertex_position(row, position_columns, inv_view_proj=None, view_origin=None):
    position_values = [float(row[column]) for column in position_columns]
    if inv_view_proj is None:
        return position_values[:3]

    world_coords = multiply_vector_matrix(position_values, inv_view_proj)
    if world_coords[3] != 0:
        world_coords = [value / world_coords[3] for value in world_coords]
    vertex_position = world_coords[:3]
    if view_origin is not None:
        vertex_position = [value + origin for value, origin in zip(vertex_position, view_origin)]
    return vertex_position


def transform_vertex_for_obj(vertex_position):
    x_value, y_value, z_value = vertex_position
    return [x_value, z_value, y_value]


def get_vertex_uv(row, uv_columns):
    if uv_columns is None:
        return None
    return [float(row[uv_columns[0]]), 1.0 - float(row[uv_columns[1]])]


def positions_match(position_a, position_b, tolerance=1e-6):
    return all(abs(value_a - value_b) <= tolerance for value_a, value_b in zip(position_a, position_b))


def create_face(vertex_indices, texture_coord_indices, corner_order):
    if texture_coord_indices is None:
        return [vertex_indices[corner_index] for corner_index in corner_order]

    return [
        (vertex_indices[corner_index], texture_coord_indices[corner_index])
        for corner_index in corner_order
    ]


def build_triangle_list_faces(vertex_indices, texture_coord_indices=None):
    faces = []
    for face_start in range(0, len(vertex_indices), 3):
        if face_start + 2 >= len(vertex_indices):
            continue

        face_vertex_indices = vertex_indices[face_start:face_start + 3]
        face_texture_coord_indices = None
        if texture_coord_indices is not None:
            face_texture_coord_indices = texture_coord_indices[face_start:face_start + 3]
        faces.append(create_face(face_vertex_indices, face_texture_coord_indices, (0, 1, 2)))

    return faces


def build_geometry(rows, position_columns, inv_view_proj, uv_columns=None, view_origin=None):
    has_idx = all('IDX' in row and row['IDX'].strip() for row in rows)
    if not has_idx:
        vertices = []
        texture_coords = []
        for row_index, row in enumerate(rows):
            vertex_position = transform_vertex_for_obj(
                get_vertex_position(row, position_columns, inv_view_proj, view_origin)
            )
            vertices.append(vertex_position)
            vertex_uv = get_vertex_uv(row, uv_columns)
            if vertex_uv is not None:
                texture_coords.append(vertex_uv)
            if row_index < 5:
                print(f"Debug: Vertex {row_index}: {vertex_position[0]}, {vertex_position[1]}, {vertex_position[2]}")

        texture_coord_indices = None
        if uv_columns is not None:
            texture_coord_indices = list(range(1, len(texture_coords) + 1))
        faces = build_triangle_list_faces(
            list(range(1, len(vertices) + 1)),
            texture_coord_indices,
        )
        return vertices, texture_coords, faces

    vertex_positions_by_idx = {}
    ordered_idx_values = []
    texture_coords = []
    ordered_texture_coord_indices = []
    for row_index, row in enumerate(rows):
        idx_value = int(row['IDX'])
        vertex_position = transform_vertex_for_obj(
            get_vertex_position(row, position_columns, inv_view_proj, view_origin)
        )
        existing_position = vertex_positions_by_idx.get(idx_value)
        if existing_position is None:
            vertex_positions_by_idx[idx_value] = vertex_position
        elif not positions_match(existing_position, vertex_position):
            print(f"Warning: IDX {idx_value} maps to multiple positions, falling back to row-based faces.")
            return build_geometry_without_idx(rows, position_columns, inv_view_proj, uv_columns)

        ordered_idx_values.append(idx_value)
        vertex_uv = get_vertex_uv(row, uv_columns)
        if vertex_uv is not None:
            texture_coords.append(vertex_uv)
            ordered_texture_coord_indices.append(len(texture_coords))
        if row_index < 5:
            print(f"Debug: Vertex {row_index}: {vertex_position[0]}, {vertex_position[1]}, {vertex_position[2]}")

    idx_to_obj_index = {
        idx_value: obj_index
        for obj_index, idx_value in enumerate(vertex_positions_by_idx.keys(), start=1)
    }
    vertices = list(vertex_positions_by_idx.values())
    ordered_vertex_indices = [idx_to_obj_index[idx_value] for idx_value in ordered_idx_values]
    texture_coord_indices = None
    if uv_columns is not None:
        texture_coord_indices = ordered_texture_coord_indices
    faces = build_triangle_list_faces(ordered_vertex_indices, texture_coord_indices)
    return vertices, texture_coords, faces


def build_geometry_without_idx(rows, position_columns, inv_view_proj, uv_columns=None, view_origin=None):
    vertices = []
    texture_coords = []
    for row_index, row in enumerate(rows):
        vertex_position = transform_vertex_for_obj(
            get_vertex_position(row, position_columns, inv_view_proj, view_origin)
        )
        vertices.append(vertex_position)
        vertex_uv = get_vertex_uv(row, uv_columns)
        if vertex_uv is not None:
            texture_coords.append(vertex_uv)
        if row_index < 5:
            print(f"Debug: Vertex {row_index}: {vertex_position[0]}, {vertex_position[1]}, {vertex_position[2]}")

    texture_coord_indices = None
    if uv_columns is not None:
        texture_coord_indices = list(range(1, len(texture_coords) + 1))
    faces = build_triangle_list_faces(
        list(range(1, len(vertices) + 1)),
        texture_coord_indices,
    )
    return vertices, texture_coords, faces


def write_obj_file(obj_output_file, vertices, texture_coords, faces):
    with open(obj_output_file, 'w') as objfile:
        for v in vertices:
            objfile.write(f"v {v[0]} {v[1]} {v[2]}\n")
        for vt in texture_coords:
            objfile.write(f"vt {vt[0]} {vt[1]}\n")
        for f in faces:
            if texture_coords:
                objfile.write(
                    f"f {f[0][0]}/{f[0][1]} {f[1][0]}/{f[1][1]} {f[2][0]}/{f[2][1]}\n"
                )
            else:
                objfile.write(f"f {f[0]} {f[1]} {f[2]}\n")


def process_csv_file(csv_file, args, requested_view_matrix_file):
    print(f"Processing: {csv_file}")

    with open(csv_file, 'r') as infile:
        reader = csv.DictReader(infile, skipinitialspace=True)
        if not reader.fieldnames:
            print(f"Warning: {csv_file} has no headers!")
            return False

        export_type, position_columns, detection_message = detect_export_type(reader.fieldnames)
        if position_columns is None:
            print(f"Warning: {csv_file} does not contain usable position data, skipping.")
            return False
        if detection_message is not None:
            print(detection_message)

        print(
            f"Detected {get_export_label(export_type)} export from headers; using position columns {position_columns}."
        )

        uv_columns = None
        if export_type == VS_INPUT_EXPORT:
            uv_columns = find_vs_input_uv_columns(reader.fieldnames, position_columns)
            if uv_columns is not None:
                print(f"Detected VS Input UV columns {uv_columns}.")
        elif export_type == VS_OUTPUT_EXPORT:
            uv_columns = find_vs_output_uv_columns(reader.fieldnames)
            if uv_columns is not None:
                print(f"Detected VS Output UV columns {uv_columns}.")

        rows = list(reader)

    if not rows:
        print(f"Warning: {csv_file} has no data rows, skipping.")
        return False

    inv_view_proj = None
    view_origin = None
    if args.no_view_transform:
        print("Skipping view transform because --no-view-transform was specified.")
    elif export_type == VS_OUTPUT_EXPORT:
        view_matrix_file = requested_view_matrix_file
        if not os.path.exists(view_matrix_file):
            print(f"Could not find {view_matrix_file} for detected VS Output export!")
            return False

        try:
            view_proj_matrix = load_view_proj_matrix(view_matrix_file)
            inv_view_proj = invert_matrix(view_proj_matrix)
            view_origin = load_view_origin(view_matrix_file)
        except ValueError as error:
            print(f"Warning: {error}")
            return False

        print(f"Loaded view-projection matrix from {view_matrix_file}")
        if view_origin is not None:
            print(f"Loaded view origin {view_origin} from {view_matrix_file}")
        else:
            print("No view origin found; reconstructed VS Output positions will remain in translated-world space.")
    else:
        if args.view_matrix_file is not None:
            print(f"Detected VS Input export; ignoring --view {requested_view_matrix_file}.")
        elif os.path.exists(requested_view_matrix_file):
            print(f"Detected VS Input export; ignoring {requested_view_matrix_file}.")
        else:
            print("Detected VS Input export; using raw position columns.")

    print(f"Loaded {len(rows)} vertices from {csv_file}.")

    vertices, texture_coords, faces = build_geometry(
        rows,
        position_columns,
        inv_view_proj,
        uv_columns,
        view_origin,
    )

    output_base_name = os.path.splitext(os.path.basename(csv_file))[0]
    obj_output_file = os.path.join(args.output_dir, f"{output_base_name}.obj")
    write_obj_file(obj_output_file, vertices, texture_coords, faces)

    print(f"Done: {len(vertices)} Vertices, {len(faces)} Faces")
    print(f"Wrote {obj_output_file}")
    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description='Generate OBJ files from Unreal Engine RenderDoc CSV exports. The script normally auto-detects VS Input exports from ATTRIBUTE0.x/y/z and VS Output exports from SV_Position.x/y/z/w.',
    )
    parser.add_argument(
        'mesh_csv_files',
        nargs='*',
        help='Mesh CSV exports from RenderDoc. If omitted, all CSVs in --input-dir except view.csv are used.'
    )
    parser.add_argument(
        '--view',
        dest='view_matrix_file',
        default=None,
        help='Optional path to a RenderDoc constant-buffer CSV export containing TranslatedWorldToClip and view-origin fields. Used automatically for SV_Position / VS Output exports. Default: TO_EXPORT/view.csv'
    )
    parser.add_argument(
        '--no-view-transform',
        action='store_true',
        help='Optional override: force raw/object-space export and skip view.csv even if the CSV looks like SV_Position / VS Output data'
    )
    parser.add_argument(
        '--input-dir',
        default='TO_EXPORT',
        help='Directory to scan for mesh CSVs when none are passed explicitly. Default: TO_EXPORT'
    )
    parser.add_argument(
        '--output-dir',
        default='EXPORT_OUT',
        help='Directory for OBJ outputs. Default: EXPORT_OUT'
    )
    return parser.parse_args()


def find_mesh_csv_files(args, excluded_csv_files):
    if args.mesh_csv_files:
        return args.mesh_csv_files

    excluded_paths = set()
    for excluded_csv_file in excluded_csv_files:
        if excluded_csv_file is not None:
            excluded_paths.add(os.path.abspath(excluded_csv_file))
    csv_files = []
    for file_name in os.listdir(args.input_dir):
        full_path = os.path.join(args.input_dir, file_name)
        if not file_name.endswith('.csv'):
            continue
        if os.path.abspath(full_path) in excluded_paths:
            continue
        csv_files.append(full_path)
    return sorted(csv_files)


args = parse_args()
default_view_matrix_file = os.path.join(args.input_dir, 'view.csv')
requested_view_matrix_file = args.view_matrix_file or default_view_matrix_file
csv_files = find_mesh_csv_files(args, [default_view_matrix_file, requested_view_matrix_file])

os.makedirs(args.output_dir, exist_ok=True)

if not csv_files:
    print("Could not find any mesh CSV files!")
    sys.exit(1)

processed_file_count = 0
for csv_file in csv_files:
    if process_csv_file(csv_file, args, requested_view_matrix_file):
        processed_file_count += 1

if processed_file_count == 0:
    print("Could not find any valid mesh CSV rows to process!")
    sys.exit(1)