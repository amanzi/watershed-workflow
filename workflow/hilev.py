"""Watershed Workflow finds, downloads, and processes data for use in hydrologic models.

This package is a python library of tools for interacting with a wide range of
data streams using free and open (both free as in freedom and free as in free
beer) python and GIS libraries and data.  Critically, this package provides a
way for **automatically and quickly** downloading, interpreting, and processing
data needed to **generate a "first" hyper-resolution simulation on any
watershed** in the conterminous United States (and most of Alaska/Hawaii/Puerto
Rico).

To do this, this package provides tools to automate finding (via REST APIs and
other approaches) data streams from **open data streams,** from United States
governmental agencies, including USGS, USDA, DOE, and others.  These data
streams are then colocated on a mesh which is generated based on a watershed
delineation and a river network, and that mesh is written in one of a variety
of mesh formats for use in hyper-resolution simulation tools.

This top level module provides functionality for getting shapes and rasters
representing watershed boundaries, river networks, digital elevation models,
and other GIS datasets and then processing those data sets for use in
simulations.

"""

import numpy as np
import matplotlib.pyplot as plt
import logging
import math

import rasterio
import rasterio.transform
import rasterio.features

import shapely

from workflow_tpls import vtk_io

import workflow.conf
import workflow.triangulation
import workflow.warp
import workflow.plot
import workflow.river_tree
import workflow.split_hucs
import workflow.hydrography
import workflow.sources.utils 
import workflow.sources.manager_shape

__all__ = ['get_huc', 'get_hucs', 'get_split_form_hucs',
           'get_shapes', 'get_split_form_shapes', 'get_reaches',
           'get_raster_on_shape', 'get_masked_raster_on_shape',
           'find_huc', 'simplify_and_prune',
           'triangulate',
           'elevate', 'values_from_raster', 'color_raster_from_shapes']

#
# functions for getting objects
# -----------------------------------------------------------------------------

def get_huc(source, huc, crs=None, digits=None):
    """Get a HUC shape object from a given code.

    Parameters
    ----------
    source : source-type
        source object providing `get_hucs()`
    huc : str
        hydrologic unit code
    crs : crs-type
        Output coordinate system.  Default is source's crs.
    digits : int
        Number of digits to round coordinates to.  Default set by config file.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of `out`.
    out : Polygon
        shapely polygon for the hydrologic unit.

    """
    huc = workflow.sources.utils.huc_str(huc)
    crs, hu_shapes = get_hucs(source, huc, len(huc), crs)
    assert(len(hu_shapes) == 1)
    return crs, hu_shapes[0]


def get_hucs(source, huc, level, crs=None, digits=None):
    """Get shape objects for all HUCs at a level contained in huc.

    Parameters
    ----------
    source : source-type
        source object providing `get_hucs()`
    huc : str
        hydrologic unit code
    level : int
        HUC level of the requested sub-basins
    crs : crs-type
        Output coordinate system.  Default is source's crs.
    digits : int
        Number of digits to round coordinates to.  Default set by config file.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of all entries in `out`.
    out : list(Polygon)
        List of shapely polygons for the subbasins.

    """
    # get the hu from source
    huc = workflow.sources.utils.huc_str(huc)
    if level is None:
        level = len(huc)

    logging.info("")
    logging.info("Preprocessing HUC")
    logging.info("-"*30)
    logging.info("Loading level {} HUCs in {}.".format(level, huc))
    
    profile, hus = source.get_hucs(huc, level)
    logging.info('  found {} HUCs.'.format(len(hus)))
    for hu in hus:
        logging.info('  -- {}'.format(hu['properties']['HUC{:d}'.format(level)]))
    
    # convert to destination crs
    native_crs = workflow.crs.from_fiona(profile['crs'])
    if crs and not workflow.crs.equal(crs, native_crs):
        for hu in hus:
            workflow.warp.shape(hu, native_crs, crs)
    else:
        crs = native_crs

    # round
    if digits is None:
        digits = workflow.conf.rcParams['digits']
    workflow.utils.round(hus, digits)

    # convert to shapely
    hu_shapes = [workflow.utils.shply(hu) for hu in hus]
    return crs, hu_shapes


def get_split_form_hucs(source, huc, level=None, crs=None, digits=None):
    """Get a SplitHUCs object for all HUCs at level contained in huc.

    A SplitHUCs object is an object which stores a collection of polygons which
    share boundaries in a format that makes changing those shared boundaries
    possible without having to update all shapes that share the boundary.

    Parameters
    ----------
    source : source-type
        source object providing `get_hucs()`
    huc : str
        hydrologic unit code
    level : int
        HUC level of the requested sub-basins
    crs : crs-type
        Output coordinate system.  Default is source's crs.
    digits : int
        Number of digits to round coordinates to.  Default set by config file.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of `out`.
    out : SplitHUCs
        Split-form HUCs object containing subbasins.

    """
    crs, hu_shapes = get_hucs(source, huc, level, crs)
    return crs, workflow.split_hucs.SplitHUCs(hu_shapes)


def get_shapes(source, index_or_bounds=None, crs=None, digits=None):
    """Read a shapefile.

    If index_or_bounds is a bounding box, crs must not be None and is the crs
    of the bounding box.

    Parameters
    ----------
    source : str or source-type
        Filename to parse, or a source object providing the get_shapes()
        method.
    index_or_bounds : int or [x_min, y_min, x_max, y_max] bounds, optional
        Filter the file, either by selecting a specific shape by index of the
        requested shape in the file, or providing a bounds-type tuple to select
        only shapes that intersect with the bounding box.
    crs : crs-type, optional
        Coordinate system of out and/or the bounding box provided in `filter`.
        The default is source's crs.
    digits : int, optional
        Number of digits to round coordinates to.  Default set by config file.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of `out`.
    out : list(shapely)
        List of shapely objects in the shapefile meeting the criteria.

    """
    logging.info("")
    logging.info("Preprocessing Shapes")
    logging.info("-"*30)

    # load shapefile
    if type(source) is str:
        logging.info('loading file: "{}"'.format(source))
        source = workflow.sources.manager_shape.FileManagerShape(source)

    profile, shps = source.get_shapes(index_or_bounds, crs)

    # convert to destination crs
    native_crs = workflow.crs.from_fiona(profile['crs'])
    if crs and not workflow.crs.equal(crs, native_crs):
        for shp in shps:
            workflow.warp.shape(shp, native_crs, crs)
    else:
        crs = native_crs
        
    # round
    if digits is None:
        digits = workflow.conf.rcParams['digits']
    workflow.utils.round(shps, digits)

    # convert to shapely
    shplys = [workflow.utils.shply(shp) for shp in shps]
    return crs, shplys

def get_split_form_shapes(source, index_or_bounds=-1, crs=None, digits=None):
    """Read a shapefile.

    Note that if index_or_bounds is a bounding box, crs must not be None and is
    the crs of the bounding box.

    Parameters
    ----------
    source : str or source-type
        Filename to parse, or a source object providing the get_shapes() method.
    index_or_bounds : int or [x_min, y_min, x_max, y_max] bounds, optional
        Filter the shapes, either by selecting a specific shape by index of the
        requested shape in the file, or providing a bounds-type tuple to select
        only shapes that intersect with the bounding box.
    crs : crs-type, optional
        Coordinate system of out and/or the bounding box provided in `filter`.
        The default is source's crs.
    digits : int, optional
        Number of digits to round coordinates to.  Default set by config file.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of `out`.
    out : SplitHUCs
        Split-form polygons object containing subbasins.

    """
    crs, shapes = get_shapes(source, index_or_bounds, crs, digits)
    return crs, workflow.split_hucs.SplitHUCs(shapes)


def get_reaches(source, huc, bounds=None, crs=None, digits=None, long=None, merge=True):
    """Get reaches from hydrography source within a given HUC and/or bounding box.

    Collects reach datasets within a HUC and/or a bounding box.  If bounds are
    provided, a containing HUC must still be provided to give a hint for file
    downloads.  If bounds are not provided, then all reaches that intersect the
    HUC are included.

    If bounds is provided, crs must not be None and is the crs of the bounding box.

    Parameters
    ----------
    source : source-type
        Source object providing a get_hydro() method.
    huc : str
        HUC containing reaches.  If bounds are provided, a hint to help the
        source find the file containing the bounds.  For NHD, this is a HUC4 or
        smaller.
    bounds : [xmin, ymin, xmax, ymax] bounds, optional
        Bounding box to filter the river network.
    crs : crs-type, optional
        Output coordinate system and coordinate system of bounds.  Defaults to
        the source's crs.
    digits : int, optional
        Number of digits to round coordinates to.  Default set by config file.
    long : float, optional
        If a reach is longer than this value it gets filtered.  Some NHD data
        has QC issues, or other wierd extra-long, single segment reaches that
        don't make sense...
    merge : bool, optional
        If true, reaches are merged (via shapely.ops.linemerge), collapsing 
        connected, non-branching reaches into a single LineString.

    Returns
    -------
    out_crs : crs-type
        Coordinate system of `out`.
    out : list(LineString)
        Reaches in the HUC and/or intersecting the bounds.

    """
    logging.info("")
    logging.info("Preprocessing Hydrography")
    logging.info("-"*30)
    logging.info("Loading streams in HUC {}".format(huc))
    logging.info("         and/or bounds {}".format(bounds))

    # get the reaches
    profile, reaches = source.get_hydro(huc, bounds, crs)
    logging.info("  found {} reaches".format(len(reaches)))

    # convert to destination crs
    native_crs = workflow.crs.from_fiona(profile['crs'])
    if crs and not workflow.crs.equal(crs, native_crs):
        for reach in reaches:
            workflow.warp.shape(reach, native_crs, crs)
    else:
        crs = native_crs

    # round
    if digits is None:
        digits = workflow.conf.rcParams['digits']
    workflow.utils.round(reaches, digits)

    # convert to shapely
    reaches_s = [workflow.utils.shply(reach) for reach in reaches]

    if merge:
        reaches_s = list(shapely.ops.linemerge(shapely.geometry.MultiLineString(reaches_s)))

    # not too long
    if long is not None:
        reaches_s = [reach for reach in reaches_s if reach.length() < long]

    return crs, reaches_s


def get_raster_on_shape(source, shape, crs, raster_crs=None, buffer=0.):
    """Collects a raster DEM that covers the requested shape.

    Parameters
    ----------
    source : source-type
        Source object providing a get_raster() method.
    shape : Polygon
        Shapely or fiona polygon on which to get the raster.
    crs : crs-type
        CRS of shape.
    raster_crs : crs-type, optional
        Output crs.  Defaults to the source's crs.
    buffer : double, optional
        Size of a buffer, in units of the shape's CRS, added to shape to ensure
        pixels cover the entire shape.  Default is 0.

    Returns
    -------
    profile : dict
        Rasterio profile of the image including rasterio CRS and transform
    raster : ndarray
        The raster data in a 2D-array.

    """
    logging.info("")
    logging.info("Preprocessing Raster")
    logging.info("-"*30)

    if type(shape) is dict:
        shape = workflow.utils.shply(shape)
    if type(shape) is shapely.geometry.MultiPolygon:
        shape = shapely.ops.cascaded_union(shape)
    shape = shape.buffer(buffer)

    logging.info("collecting raster")
    profile, raster = source.get_raster(shape, crs)

    # warp the raster to the requested output
    if raster_crs is not None:
        profile, raster = workflow.warp.raster(profile, raster, raster_crs)

    return profile, raster


def get_masked_raster_on_shape(source, shape, crs, nodata=-1, buffer=0.):
    """Collects a raster that is masked to the requested shape.

    Parameters
    ----------
    source : source-type
        Source object providing a get_raster() method.
    shape : Polygon
        Shapely or fiona polygon on which to get the raster.
    crs : crs-type
        CRS of shape.
    nodata : dtype, optional
        Value to place in the array outside of shape.  Note that the type of
        this value should be the same as the data in the raster.  Default is
        -1.
    buffer : double, optional
        Size of a buffer, in units of the shape's CRS, added to shape to ensure
        pixels cover the entire shape.  Default is 0.

    Returns
    -------
    profile : dict
        Rasterio profile of the image including rasterio CRS and transform
    raster : ndarray
        The raster data in a 2D-array.

    """
    # get the raster
    profile, raster = get_raster_on_shape(source, shape, crs, crs, buffer)

    # mask the raster
    mask = rasterio.features.geometry_mask([shape,], raster.shape, profile['transform'], invert=True)
    masked_raster = np.where(mask, raster, nodata)

    transform = profile['transform']
    x0 = transform * (0,0)
    x1 = transform * (profile['width'], profile['height'])
    logging.info(" raster bounds = {}".format((x0[0], x0[1], x1[0], x1[1])))
    return profile, masked_raster


#
# functions for relating objects
# -----------------------------------------------------------------------------

def find_huc(source, shape, crs, hint, shrink_factor=1.e-5):
    """Finds the smallest HUC containing shp.

    Parameters
    ----------
    source : source-type
        Source object providing a get_hucs() method.
    shape : Polygon
        Shapely or fiona polygon on which to get the raster.
    crs : crs-type
        CRS of shape.
    hint : str
        HUC in which to start searching.  This should be at least as long as
        the indexing file size -- HUC 2 or longer for WBD, 4 or longer for NHD
        Plus, or 8 or longer for NHD.
    shrink_factor : float, optional
        A fraction of the radius of shape to shrink prior for checking
        containment within HUCs.  This fixes cases where shape is on a HUC
        boundary with potentially some numerical error.

    Returns
    ------- 
    out : str
        The smallest containing HUC.

    """
    def _in_huc(shply, huc_shply):
        """Checks whether shp is in HUC"""
        if huc_shply.contains(shply):
            return 2
        elif huc_shply.intersects(shply):
            return 1
        else:
            return 0

    def _find_huc(source, shply, crs, hint):
        """Searches in hint to find shp."""
        logging.debug('searching: %s'%hint)
        hint_level = len(hint)
        search_level = hint_level + 2
        if search_level > source.lowest_level:
            return hint

        profile, subhus = source.get_hucs(hint, search_level)
        native_crs = workflow.crs.from_fiona(profile['crs'])
        
        for subhu in subhus:
            workflow.warp.shape(subhu, native_crs, crs)
            subhu_shply = workflow.utils.shply(subhu['geometry'])        
            inhuc = _in_huc(shply, subhu_shply)

            if inhuc == 2:
                # fully contained in try_huc, recurse
                hname = subhu['properties']['HUC{:d}'.format(search_level)]
                logging.debug('  subhuc: %s contains'%hname)
                return _find_huc(source, shply, crs, hname)
            elif inhuc == 1:
                hname = subhu['properties']['HUC{:d}'.format(search_level)]
                logging.debug('  subhuc: %s partially contains'%hname)
                # partially contained in try_huc, return this
                return hint
            else:
                hname = subhu['properties']['HUC{:d}'.format(search_level)]
                logging.debug('  subhuc: %s does not contain'%hname)
        assert(False)

    if type(shape) is shapely.geometry.Polygon:
        shply = shape
    else:
        shply = workflow.utils.shply(shape)

    # must shrink the poly a bit in case it is close to or on a boundary
    radius = np.sqrt(shply.area/np.pi)
    shply_s = shply.buffer(-shrink_factor*radius)

    hint = workflow.sources.utils.huc_str(hint)

    profile, hint_hu = source.get_huc(hint)
    native_crs = workflow.crs.from_fiona(profile['crs'])
    workflow.warp.shape(hint_hu, native_crs, crs)
    
    inhuc = _in_huc(shply_s, workflow.utils.shply(hint_hu['geometry']))
    if inhuc is not 2:
        raise RuntimeError("{}: shape not found in hinted HUC '{}'".format(source.name, hint))

    result = _find_huc(source, shply_s, crs, hint)
    return result


def simplify_and_prune(hucs, reaches, simplify=10, prune_reach_size=0, cut_intersections=False):
    """Cleans up the HUC and river shapes.

    Ensures intersections are proper, snapped, simplified, etc.  Note, HUCs and
    rivers must be in the same crs.

    .. note: 
        This also may modify the hucs object in-place.

    Parameters
    ----------
    hucs : SplitHUCs
        A split-form HUC object containing all reaches.
    reaches : list(LineString)
        A list of reaches.
    simplify : float, optional
        Argument to shapely's simplify, a measure of how far to allow shapes to
        move.  Default is 10 (units are in that of the CRS of hucs and
        reaches).
    prune_river_size : int, optional
        Remove rivers with fewer than this number of reaches.  Default is 0.
    cut_intersections : bool
        Cut HUC segments at the river input/output, potentially resulting in
        simpler geometries.  This is work in progress.  Default is False.

    Returns
    ------- 
    out : list(RiverTree)
        A list of rivers, as RiverTree objects.

    """
    tol = simplify
    
    logging.info("")
    logging.info("Simplifying and pruning")
    logging.info("-"*30)
    logging.info("Filtering rivers outside of the HUC space")
    reaches = workflow.hydrography.filter_rivers_to_shape(hucs.exterior(), reaches, tol)
    if len(reaches) is 0:
        return reaches

    logging.info("Generate the river tree")
    rivers = workflow.hydrography.make_global_tree(reaches)

    logging.info("Removing rivers with fewer than {} reaches.".format(prune_reach_size))
    for i in reversed(range(len(rivers))):
        ltree = len(rivers[i])
        if ltree < prune_reach_size:
            rivers.pop(i)
            logging.info("  ...removing river with %d reaches"%ltree)
        else:
            logging.info("  ...keeping river with %d reaches"%ltree)
    if len(rivers) is 0:
        return rivers
            
    logging.info("simplifying rivers")
    workflow.hydrography.cleanup(rivers, tol, tol, tol)

    logging.info("simplifying HUCs")
    workflow.split_hucs.simplify(hucs, tol)

    # snap
    logging.info("snapping rivers and HUCs")
    rivers = workflow.hydrography.snap(hucs, rivers, tol, 3*tol, cut_intersections)
    
    logging.info("")
    logging.info("Simplification Diagnostics")
    logging.info("-"*30)
    if len(rivers) is not 0:
        mins = []
        for river in rivers:
            for line in river.dfs():
                coords = np.array(line.coords[:])
                dz = np.linalg.norm(coords[1:] - coords[:-1], 2, -1)
                mins.append(np.min(dz))
        logging.info("  river min seg length: %g"%min(mins))
        logging.info("  river median seg length: %g"%np.median(np.array(mins)))

    mins = []
    for line in hucs.segments:
        coords = np.array(line.coords[:])
        dz = np.linalg.norm(coords[1:] - coords[:-1], 2, -1)
        mins.append(np.min(dz))
    logging.info("  HUC min seg length: %g"%min(mins))
    logging.info("  HUC median seg length: %g"%np.median(np.array(mins)))
    return rivers
    
def triangulate(hucs, rivers, diagnostics=True, verbosity=1,
                refine_max_area=None, refine_distance=None, refine_max_edge_length=None,
                refine_min_angle=None, enforce_delaunay=False):
    """Triangulates HUCs and rivers.

    Note, refinement of a given triangle is done if any of the provided
    criteria is met.

    Parameters
    ----------
    hucs : SplitHUCs
        A split-form HUC object from, e.g., get_split_form_hucs()
    reaches : list(LineString)
        A list of reaches from, e.g., get_reaches()
    diagnostics : bool, optional
        Plot diagnostics graphs of the triangle refinement.    
    refine_max_area : float, optional
        Refine a triangle if its area is greater than this area.
    refine_distance : list(float), optional
        Refine a triangle if its area is greater than a function of its
        centroid's distance from the nearest point on the river network.  The
        argument is given by:

        [near_distance, near_area, far_distance, far_area]

        Defining d as the distance from triangle centroid to the nearest point
        on the river network and area as the area of the triangle in question,
        refinement occurs if:

        * d < near_distance and area > near_area
        * d > far_distance and area > far_area
        * otherwise, defining 
          d' = (d - near_distance) / (far_distance - near_distance),
          refining occurs if
          area > near_area + (far_area - near_area) * d'

        Effectively this simply writes a piecewise linear function of triangle
        distance from centroid and uses that as a max area criteria.
    refine_max_edge_length : float, optional
        Refine a triangle if its max edge length is greater than this length.
    refine_min_angle : float, optional
        Try to ensure that all triangles have a minimum edge length greater
        than this value.
    enforce_delaunay : bool,optional, experimental
        Attempt to ensure all triangles are proper Delaunay triangles.

        .. note:
            This requires a hacked version of meshpy.triangle that
            supports this option.  See the patch available at
            workflow_tpls/meshpy_triangle.patch

    Returns
    -------
    vertices : np.array((n_points, 2), 'd')
        Array of triangle vertices.
    triangles : np.array((n_tris, 3), 'i')
        For each triangle, a list of 3 indices into the vertex array that make
        up that triangle.

    """
    verbose = verbosity > 2
    
    logging.info("")
    logging.info("Meshing")
    logging.info("-"*30)

    refine_funcs = []
    if refine_max_area is not None:
        refine_funcs.append(workflow.triangulation.refine_from_max_area(refine_max_area))
    if refine_distance is not None:
        refine_funcs.append(workflow.triangulation.refine_from_river_distance(*refine_distance, rivers))
    if refine_max_edge_length is not None:
        refine_funcs.append(workflow.triangulation.refine_from_max_edge_length(refine_max_edge_length))
    def my_refine_func(*args):
        return any(rf(*args) for rf in refine_funcs)        

    vertices, triangles = workflow.triangulation.triangulate(hucs, rivers,
                                                             verbose=verbose,
                                                             refinement_func=my_refine_func,
                                                             min_angle=refine_min_angle,
                                                             enforce_delaunay=enforce_delaunay)

    if diagnostics:
        logging.info("Plotting triangulation diagnostics")
        river_multiline = workflow.river_tree.forest_to_list(rivers)
        distances = []
        areas = []
        needs_refine = []
        for tri in triangles:
            verts = vertices[tri]
            bary = np.sum(np.array(verts), axis=0)/3
            bary_p = shapely.geometry.Point(bary[0], bary[1])
            distances.append(bary_p.distance(river_multiline))
            areas.append(workflow.utils.triangle_area(verts))
            needs_refine.append(my_refine_func(verts, areas[-1]))

        if verbosity > 0:
            plt.figure()
            plt.subplot(121)
            plt.hist(distances)
            plt.xlabel("distance from river of triangle centroids [m]")
            plt.ylabel("count [-]")
            plt.subplot(122)
            plt.scatter(distances, areas,c=needs_refine,marker='x')
            plt.xlabel("distance [m]")
            plt.ylabel("triangle area [m^2]")

            # plt.figure()
            # plt.subplot(111)
            # workflow.plot.hucs(hucs)
            # workflow.plot.rivers(rivers)
            # workflow.plot.triangulation(vertices, triangles, areas)
            # plt.title("triangle area [m^2]")
    return vertices, triangles

def elevate(mesh_points, mesh_crs, dem, dem_profile, algorithm='piecewise bilinear'):
    """Elevate mesh_points onto the provided dem.

    Parameters
    ----------
    mesh_points : np.array((n_points, 2), 'd')
        Array of triangle vertices.
    mesh_crs : crs-type
        Mesh coordinate system.
    dem : np.array
        2D array forming an elevation raster.
    dem_profile : dict
        rasterio profile for the elevation raster.
    algorithm : str, optional
        Algorithm used for interpolation.  One of:
        * "nearest" for nearest-neighbor pixels
        * "piecewise bilinear" for interpolation

    Returns
    -------
    out : np.array((n_points, 3), 'd')
        Array of triangle vertices, including a z-dimension.

    """
    logging.info("")
    logging.info("Elevating Triangulation to DEM")
    logging.info("-"*30)

    # index the i,j of the points, pick the elevations
    elev = values_from_raster(mesh_points, mesh_crs, dem, dem_profile, algorithm)

    # create the 3D points
    out = np.zeros((len(mesh_points),3),'d')
    out[:,0:2] = mesh_points
    out[:,2] = elev
    return out


def values_from_raster(points, points_crs, raster, raster_profile, algorithm='nearest'):
    """Interpolate a raster onto a collection of unstructured points.

    Parameters
    ----------
    points : np.array((n_points, 2), 'd')
        Array of points to interpolate onto.
    points_crs : crs-type
        Coordinate system of the points.
    raster : np.array
        2D array forming the raster.
    raster_profile : dict
        rasterio profile for the raster.
    algorithm : str
        Algorithm used for interpolation.  One of:
        * "nearest" for nearest neighbor pixels
        * "piecewise bilinear" for interpolation

    Returns
    -------
    out : np.array((n_points,))
        Array of raster values interpolated onto the points.

    """
    raster_crs = workflow.crs.from_rasterio(raster_profile['crs'])
    points_raster_crs = np.array(workflow.warp.xy(points[:,0], points[:,1], points_crs, raster_crs)).transpose()
    if algorithm == 'nearest':
        out = raster[rasterio.transform.rowcol(raster_profile['transform'], points_raster_crs[:,0], points_raster_crs[:,1])]
    elif algorithm == 'piecewise bilinear':
        eps = 1.e-10
        
        # get the index of the point
        invtransform = ~raster_profile['transform']
        mybox = np.zeros((2,2),'d')
        out = np.zeros((len(points),),'d')
        for k,xy in enumerate(points_raster_crs):
            xy = tuple(xy)
            j,i = invtransform * xy

            # center on pixel
            i -= 0.5
            j -= 0.5
            
            i = max(eps, min(raster_profile['height']-1-eps, i))
            j = max(eps, min(raster_profile['width']-1-eps, j))

            mybox[0,0] = raster[math.floor(i), math.floor(j)]
            mybox[0,1] = raster[math.floor(i), math.ceil(j)]
            mybox[1,0] = raster[math.ceil(i), math.floor(j)]
            mybox[1,1] = raster[math.ceil(i), math.ceil(j)]
            ii = i%1
            jj = j%1

            up = mybox[0,0] + jj * (mybox[0,1] - mybox[0,0])
            dn = mybox[1,0] + jj * (mybox[1,1] - mybox[1,0])
            out[k] = up + (dn - up) * ii
    return out
    

def color_raster_from_shapes(target_bounds, target_dx, shapes, shape_colors,
                             shapes_crs, nodata=-1):
    """Color in a raster by filling in a collection of shapes.

    Given a canvas specified by bounds and pixel size, color a raster by, for
    each shape, finding the intersection of that shape with the canvas and
    coloring it by a provided value.  Paint by numbers.

    Note, if the shapes overlap, the last shape containing a pixel gives the
    color of that pixel.

    Parameters
    ----------
    target_bounds : [xmin, ymin, xmax, ymax]
        Bounding box for the output raster.
    target_dx : float
        Pixel size (assumed the same in both x and y).
    shapes : list(Polygon)
        Collection of shapes (likely) overlapping the canvas.
    shapes_colors : np.array((n_shapes,), dtype)
        Color to label the interior of each polygon with.
    shapes_crs : crs-type
        Coordinate system of the shapes.
    nodata : dtype, optional
        Value to place in pixels which intersect no shape.  Note the type of
        this should be the same as the type of shape_colors.  Default is -1.

    Returns
    -------
    out : np.array(target_bounds, dtype)
        Raster of colors.
    out_profile : dict
        rasterio profile of the color raster.
    out_bounds : [x_min, y_min, x_max, y_max]
        Physial bounds of the resulting image.

    """
    assert(len(shapes) == len(shape_colors))
    assert(len(shapes) > 0)
    
    dtype = np.dtype(type(shape_colors[0]))
    
    target_x0 = np.round(target_bounds[0] - target_dx/2)
    target_y1 = np.round(target_bounds[3] + target_dx/2)
    width = int(np.ceil((target_bounds[2] + target_dx/2 - target_x0)/target_dx))
    height = int(np.ceil((target_y1 - target_bounds[1] - target_dx/2)/target_dx))

    out_bounds = [target_x0, target_y1 - target_dx*height, target_x0 + target_dx*width, target_y1]

    logging.info('Coloring shapes onto raster:')
    logging.info('  target_bounds = {}'.format(target_bounds))
    logging.info('  out_bounds = {}'.format(out_bounds))
    logging.info('  pixel_size = {}'.format(target_dx))
    logging.info('  width = {}, height = {}'.format(width, height))
    logging.info('  and {} independent colors of dtype {}'.format(len(set(shape_colors)), dtype))

    transform = rasterio.transform.from_origin(target_x0, target_y1, target_dx, target_dx)
    
    out_profile = {'height':height,
                      'width':width,
                      'count':1,
                      'dtype':dtype,
                      'crs':workflow.crs.to_rasterio(shapes_crs),
                      'transform':transform,
                      'nodata':nodata}
    
    out = nodata * np.ones((height, width), dtype)
    for p, p_id in zip(shapes, shape_colors):
        mask = rasterio.features.geometry_mask([p,], out.shape, transform, invert=True)
        out[mask] = p_id
    return out, out_profile, out_bounds



    
