# -*- coding: utf-8 -*-
#
"""I/O for Modlflow/Patran files.

The geometry is read from the provided *.pat file. If a file with the same
name and ending *.ele or *.nod is provided, cell data and point data is added.

This feature is designed to convert Patran files generated from Modlflow(See
https://knowledge.autodesk.com/support/moldflow-insight/learn-explore/
caas/CloudHelp/cloudhelp/2018/ENU/MoldflowInsight/files/
GUID-BCC20E1A-12EA-428F-95F5-C1E4BC1E416C-htm.html and
https://knowledge.autodesk.com/support/moldflow-insight/learn-explore/
caas/sfdcarticles/sfdcarticles/How-to-export-fiber-orientation-results
-in-XML-or-Patran-format-from-Moldflow.html)

"""

import os
import pathlib

import numpy

from ..__about__ import __version__
from .._exceptions import ReadError
from .._files import open_file
from .._helpers import register
from .._mesh import CellBlock, Mesh

pat_to_meshio_type = {
    2: "line",
    3: "triangle",
    4: "quad",
    5: "tetra",
    7: "wedge",
    8: "hexa_prism",
    9: "pyramid",
}
meshio_to_pat_type = {v: k for k, v in pat_to_meshio_type.items()}


def read(
    filename, ele_filename=None, nod_filename=None, xml_filename=None, scale=1.0,
):
    """Read a Patran *.pat file.

    If a *.ele file, *.xml file or *.nod file is provided or if it has the
    same name as the *.pat file, these files are used to fill data fields.
    Such files are exported by Modlflow for example.

    Args
    ----
        filename : str
            Patran filename that should be read

        ele_filename : str, optional
            element-wise data file

        nod_filename : str, optional
            node-wise data file.

        xml_filename : str, optional
            element-wise data file

        scale : float
            scale factor for nodes

        autoremove : boolean
            automatically delete cells with no data attached
    """
    with open(filename, "r") as f:
        mesh, element_gids, point_gids = read_pat_buffer(f, scale)

    # if *.ele file is present: Add cell data
    if ele_filename:
        ele_path = pathlib.Path(ele_filename)
        if not ele_path.exists():
            raise ReadError("File {} not found.".format(ele_filename))
        else:
            with open(ele_path, "r") as f:
                mesh = read_ele_buffer(f, mesh, element_gids)

    # if *.xml file is present: Add cell or node data
    if xml_filename:
        xml_path = pathlib.Path(xml_filename)
        if not xml_path.exists():
            raise ReadError("File {} not found.".format(xml_filename))
        else:
            mesh = read_xml_buffer(xml_path, mesh, element_gids, point_gids)

    # if *.nod file is present: Add point data
    if nod_filename:
        nod_path = pathlib.Path(nod_filename)
        if not nod_path.exists():
            raise ReadError("File {} not found.".format(nod_filename))
        else:
            with open(nod_path, "r") as f:
                mesh = read_nod_buffer(f, mesh, point_gids)

    return mesh


def read_ele_buffer(f, mesh, element_gids):
    """Read element based data file."""

    name = f.readline().replace(" ", "_").rstrip("\n")
    dimensions = f.readline().split()
    N = int(dimensions[0])
    order = int(dimensions[-1])
    f.readline()

    data = {}

    for i in range(N - 20):
        line = f.readline().split()
        ID = int(line[0])
        values = list(map(float, line[1:]))
        data[ID] = numpy.array(values)

    data_list = []
    for i, cell_block in enumerate(mesh.cells):
        elem_type = cell_block.type
        del_pos = []
        for pos, gid in enumerate(element_gids[elem_type]):
            try:
                data_list.append(data[gid])
            except KeyError:
                del_pos.append(pos)
        # delete cells with no data
        mesh.cells[i] = mesh.cells[i]._replace(
            data=numpy.delete(mesh.cells[i].data, del_pos, axis=0)
        )

    mesh.cell_data[name] = numpy.array(data_list)

    return mesh


def read_xml_buffer(xml_filename, mesh, element_gids, point_gids):
    """Read element based data file."""
    import xml.etree.ElementTree as ET

    tree = ET.parse(xml_filename)
    root = tree.getroot()

    dataset = root.find("Dataset")
    type = dataset.find("DataType").text
    order = int(dataset.find("NumberOfComponents").text)
    base_name = dataset.find("DeptVar").get("Name").replace(" ", "_").rstrip("\n")
    blocks = dataset.find("Blocks")

    for block in blocks.findall("Block"):
        data = {}
        indep = block.find("IndpVar")
        if indep is not None:
            indep_idx = block.get("Index")
            indep_name = indep.get("Name")
            indep_value = indep.get("Value")
            indep_unit = indep.get("Unit")
            name = "%s_Block:%s_%s:%s%s" % (
                base_name,
                indep_idx,
                indep_name,
                indep_value,
                indep_unit,
            )
        else:
            name = base_name

        layers = block.findall("Layer") or [block]
        for layer in layers:
            for item in layer.find("Data"):
                ID = int(item.get("ID"))
                line = item.find("DeptValues").text
                values = list(map(float, line.split()))
                data[ID] = numpy.array(values)

        if "ELDT" in type:
            data_list = []
            for i, cell_block in enumerate(mesh.cells):
                elem_type = cell_block.type
                del_pos = []
                for pos, gid in enumerate(element_gids[elem_type]):
                    try:
                        data_list.append(data[gid])
                    except KeyError:
                        del_pos.append(pos)
                # delete cells with no data.
                mesh.cells[i] = mesh.cells[i]._replace(
                    data=numpy.delete(mesh.cells[i].data, del_pos, axis=0)
                )

            mesh.cell_data[name] = numpy.array(data_list)

        elif "NDDT" in type:
            data_list = []
            for gid in point_gids:
                try:
                    values = data[gid]
                except KeyError:
                    values = numpy.nan * numpy.ones(order)
                data_list.append(values)

            mesh.point_data[name] = numpy.squeeze(numpy.array(data_list))

    return mesh


def read_nod_buffer(f, mesh, point_gids):
    """Read node based data file."""
    node_id_map = {}
    for line, id in enumerate(point_gids):
        node_id_map[id] = line

    name = f.readline().replace(" ", "_").rstrip("\n")
    dimensions = f.readline().split()
    N = len(point_gids)
    order = int(dimensions[-1])
    f.readline()

    array = numpy.nan * numpy.ones([N, order])

    for i in range(N):
        line = f.readline()
        if line:
            line = line.split()
            ID = int(line[0])
            values = list(map(float, line[1:]))
            line = node_id_map[ID]
            array[line, :] = values

    mesh.point_data = {name: numpy.squeeze(array)}
    return mesh


def read_pat_buffer(f, scale):
    """Read Patran geometry file."""
    # Initialize the optional data fields
    cells = {}
    points = []
    point_gids = []
    element_gids = {}

    while True:
        line = f.readline()
        if not line:
            # EOF
            break

        card_data = line.split()
        if line.startswith(" 1"):
            points.append(_read_node(f, scale))
            point_gids.append(int(card_data[1]))
        elif line.startswith(" 2"):
            lnodes = _read_cell(f)
            shape_code = int(card_data[2])
            key = pat_to_meshio_type[shape_code]
            if key in cells:
                cells[key].append(lnodes)
                element_gids[key].append(int(card_data[1]))
            else:
                cells[key] = [lnodes]
                element_gids[key] = [int(card_data[1])]
        elif line.startswith(" 4"):
            # do not read cross section properties.
            f.readline()

    for key in cells.keys():
        cells[key] = numpy.vstack(cells[key])

    points = numpy.array(points, dtype=float)
    point_gids = numpy.array(point_gids, dtype=int)

    cells = _scan_cells(point_gids, cells)

    return Mesh(points, cells), element_gids, point_gids


def _read_node(f, scale):
    """Read a node card.

    The node card contains the following:
    === ===== === ====== === ===
     1  ID    IV  KC
    === ===== === ====== === ===
    X   Y     Z
    ICF GTYPE NDF CONFIG CID PSP
    === ===== === ====== === ===
    """
    line = f.readline()
    entries = [line[i : i + 16] for i in range(0, len(line), 16)]
    point = [scale * float(coordinate) for coordinate in entries[:-1]]
    f.readline()
    return point


def _read_cell(f):
    """Read a cell card.

    The element card contains the following:
    ====== ====== === ==== == == ==
    2      ID     IV  KC   N1 N2
    ====== ====== === ==== == == ==
    NODES  CONFIG PID CEID q1 q2 q3
    LNODES
    ADATA
    ====== ====== === ==== == == ==
    """
    f.readline()
    entries = f.readline().split()
    lnodes = list(map(int, entries))
    lnodes = numpy.trim_zeros(lnodes)
    return numpy.array(lnodes)


def _scan_cells(point_gids, cells):
    lookup_table = dict(zip(point_gids, numpy.arange(0, len(point_gids))))
    for elem_type in cells.keys():
        vect_lookup = numpy.vectorize(lookup_table.get)
        cells[elem_type] = vect_lookup(cells[elem_type])
    return cells


def write(filename, mesh):
    """Write a dummy for now."""
    with open(filename, "wt") as f:
        f.write("DUMMY")


register("moldflow", [".pat"], read, {"moldflow": write})
