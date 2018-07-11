import os
import numpy as np
from mpl_toolkits.mplot3d.axes3d import Axes3D
import matplotlib.pyplot as plt

import shapely
import meshpy.triangle

import workflow.conf
import workflow.smooth
import workflow.download
import workflow.triangulate
import workflow.clip
import workflow.warp
import workflow.rowcol

import vtk_io # from ATS/tools/meshing_ats

myhuc = '06010208'
outdir = "data/meshes/%s/12"%myhuc
if not os.path.isdir(outdir):
    os.makedirs(outdir)

## == Preprocess HUCs ==
# collect HUC shapefile
print("downloading HUC %s"%myhuc[0:2])
workflow.download.download_huc(myhuc[0:2])

# load shapefiles for the HUC8 of interest
print("loading HUC %s"%myhuc)
profile, huc8 = workflow.conf.load_huc(myhuc)

# load shapefiles for all HUC 12s in the Obed HUC 8.
print("loading all 12s")
profile, huc12s = workflow.conf.load_hucs_in('06010208', 12)

# change coordinates to meters (in place)
print("change coordinates to m")
for huc12 in huc12s:
    workflow.warp.warp_shape(huc12, profile['crs'], workflow.conf.default_crs())

# convert to shapely
hucs_s = [shapely.geometry.shape(s['geometry']) for s in huc12s]

## == Preprocess hydrography ==
# collect hydrography
print("downloading Hydrography %s"%myhuc)
workflow.download.download_hydro(myhuc)

# load stream network
print("loading streams")
rprofile, rivers = workflow.conf.load_hydro('06010208')

# change coordinates to meters (in place)
print("change coordinates to m")
for river in rivers:
    workflow.warp.warp_shape(river, rprofile['crs'], workflow.conf.default_crs())

# convert to shapely
rivers_s = [shapely.geometry.shape(r['geometry']) for r in rivers]

## == Combine and split HUCs and Hydrography into HUC partitions ==
# combine rivers to improve simplify.  this step is lossless currently
assert(all(type(r) is shapely.geometry.LineString for r in rivers_s))
rivers_s = list(shapely.ops.linemerge(rivers_s))
assert(all(type(r) is shapely.geometry.LineString for r in rivers_s))

# split polygons into spine and boundary
uniques, intersections = workflow.smooth.intersect_and_split(hucs_s)
assert(all(type(u) is shapely.geometry.LineString or
           type(u) is shapely.geometry.MultiLineString or
           type(u) is type(None) for u in uniques))
assert(all(type(s) is shapely.geometry.LineString or
           type(s) is shapely.geometry.MultiLineString or
           type(s) is type(None) for i in intersections for s in i))

# generate a list of all spine/boundary segments
all_segs = []
for s in uniques:
    if type(s) is shapely.geometry.LineString:
        all_segs.append(s)
    elif type(s) is shapely.geometry.MultiLineString:
        for seg in s:
            assert(type(seg) is shapely.geometry.LineString)
            all_segs.append(seg)
for i in intersections:
    for s in i:
        if type(s) is shapely.geometry.LineString:
            all_segs.append(s)
        elif type(s) is shapely.geometry.MultiLineString:
            for seg in s:
                assert(type(seg) is shapely.geometry.LineString)
                all_segs.append(seg)

# intersect rivers with spine and boundary, adding points at any
# intersection to both river and spine/boundary segment
assert(all(type(r) is shapely.geometry.LineString for r in rivers_s))
assert(all(type(s) is shapely.geometry.LineString for s in all_segs))
workflow.hydrography.split_spine(all_segs, rivers_s)
assert(all(type(r) is shapely.geometry.LineString for r in rivers_s))
assert(all(type(s) is shapely.geometry.LineString for s in all_segs))

# add rivers to all segs -- this is truely now all line objects in the
# combined HUC/river shapelist
all_segs = rivers_s + all_segs
all_segs = shapely.geometry.MultiLineString(all_segs)

# simplify to coarsen
all_segs_simp = list(all_segs.simplify(100))  # units = m
assert(len(all_segs_simp) == len(all_segs))
assert(all(type(s) is shapely.geometry.LineString for s in all_segs_simp))

# check min distances
min_seg = 1.e80
for seg in all_segs_simp:
    coords = np.array(seg.coords)
    assert(coords.shape[1] == 2)
    l2 = np.linalg.norm2(coords[1:] - coords[:-1], axis=1)
    assert(len(l2) == len(coords))
    min_seg = min(min_seg, l2.min())
print("Min distance = %d"%min_seg)

# intersect, finding shared boundaries
print("intersecting to find boundary spine")
uniques, intersections = workflow.smooth.intersect_and_split(shps)





# smooth/simplify/resample to a given spacing (in meters)
print("smoothing")
uniques_sm, intersections_sm = workflow.smooth.simplify(uniques,intersections,100.)  # units = m

# recombine
print("recombine")
shps_sm = workflow.smooth.recombine(uniques_sm, intersections_sm)

# restructure back to the original format of uniques/intersections/rivers
# -- pop the rivers
rivers_simp = all_segs_simp[0:len(rivers_s)]
all_segs_simp = all_segs_simp[len(rivers_s):]

# -- next the uniques
uniques_simp = [None,]*len(uniques)
pos = 0
for i,u in enumerate(uniques):
    if type(u) is shapely.geometry.LineString:
        uniques_simp[i] = all_segs_simp[pos]
        pos += 1
    elif type(u) is shapely.geometry.MultiLineString:
        num_segs = len(u)
        uniques_simp[i] = shapely.geometry.MultiLineString(all_segs_simp[pos:pos+num_segs])
        pos += num_segs

# -- finally the intersections
intersections_simp = [[None for i in range(len(intersections))] for j in range(len(intersections))]
for i,inter in enumerate(intersections):
    for j,u in enumerate(inter):
        if type(u) is shapely.geometry.LineString:
            intersections_simp[i][j] = all_segs_simp[pos]
            pos += 1
        elif type(u) is shapely.geometry.MultiLineString:
            num_segs = len(u)
            intersections_simp[i][j] = shapely.geometry.MultiLineString(all_segs_simp[pos:pos+num_segs])
            pos += num_segs

# -- check the final tally -- we better have gotten them all            
assert(pos == len(all_segs_simp))

# recombine the simplified objects
hucs_simp = workflow.smooth.recombine(uniques_simp, intersections_simp)

# sort rivers by containing poly
rivers_part = workflow.hydrography.sort_precut(hucs_simp, rivers_simp)






# triangulate (to a refinement with max_area, units a bit unclear.
# I believe these should be degrees^2, then m^2 once in UTM, but the magnitude seems
# wrong for that.  Takes some fiddling.)
#
# Then plot the triangles.
# TODO -- add refinement function based on distance function and hydrography data
print("triangulating")
fig = plt.figure()
ax = fig.add_subplot(1,2,1)
triangles = []
for shp in shps_sm:
    mesh_points, mesh_tris = workflow.triangulate.triangulate(shp, max_area=1e5) # units = m^2?
    #ax.triplot(mesh_points[:, 0], mesh_points[:, 1], mesh_tris)
    triangles.append((mesh_points, mesh_tris))

# download and tile a DEM for this entire HUC
print("tiling with DEMs")
dem_profile, dem = workflow.clip.clip_dem(huc8)
dem = dem[0,:,:] # only the first band

# collect DEM values from the points (mostly done, add here) --etc
# -- must map back to lat/lon to take from dem
print("grabbing elevation")
ax = fig.add_subplot(1, 2, 2, projection='3d')

triangles_3d = []

for mesh_points, mesh_tris in triangles:
    mesh_points_ll = np.array(workflow.warp.warp_xy(mesh_points[:,0], mesh_points[:,1], workflow.conf.default_crs(), workflow.conf.latlon_crs())).transpose()
    elev = dem[workflow.rowcol.rowcol(dem_profile['affine'], mesh_points_ll[:,0], mesh_points_ll[:,1])]
    #ax.plot_trisurf(mesh_points[:,0], mesh_points[:,1], elev, triangles=mesh_tris)

    triangles_3d.append((np.array([mesh_points[:,0], mesh_points[:,1], elev]).transpose(), mesh_tris))
#plt.show()

# bring in other data streams? (not done) --etc

# save as a mesh
# this could be cleaner, but meshing_ats is in python2 (and uses exodus which is in python2)
for huc, mesh in zip(huc12s, triangles_3d):
    filename = os.path.join(outdir, 'huc_%s.vtk'%huc['properties']['HUC12'])
    cells = {'triangle':mesh[1]}
    vtk_io.write(filename, mesh[0], cells)