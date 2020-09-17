# -*- coding: utf-8 -*-
"""
river_utils
===========

Created on Tue Nov  6 14:29:10 2018

@author: Jon
"""
import numpy as np
import networkx as nx
from scipy.ndimage.morphology import distance_transform_edt
import shapely
from shapely.geometry import LineString, Polygon
import scipy.interpolate as si
from scipy import signal
from scipy.spatial.distance import cdist
from matplotlib import pyplot as plt
import geopandas as gpd

from rivgraph.ordered_set import OrderedSet
import rivgraph.im_utils as iu
import rivgraph.mask_to_graph as m2g
import rivgraph.ln_utils as lnu


def prune_river(links, nodes, exit_sides, Iskel, gdobj):

    # Get inlet nodes
    nodes = find_inlet_outlet_nodes(links, nodes, exit_sides, Iskel)

    # Remove spurs from network (this includes valid inlets and outlets unless specified not to remove)
    links, nodes = lnu.remove_all_spurs(links, nodes, dontremove=list(nodes['inlets'] + nodes['outlets']))

    # # Add artificial nodes where necessary
    # links, nodes = lnu.add_artificial_nodes(links, nodes, gdobj)
    links, nodes = lnu.find_parallel_links(links, nodes)

    # Remove sets of links that are disconnected from inlets/outlets except for a single bridge link (effectively re-pruning the network)
    links, nodes = lnu.remove_disconnected_bridge_links(links, nodes)

    # Remove one-pixel links
    links, nodes = lnu.remove_single_pixel_links(links, nodes)

    return links, nodes


def find_inlet_outlet_nodes(links, nodes, exit_sides, Iskel):
    """
    Appends the inlet and outlet nodes to the nodes dictionary. Only works for
    rivers; deltas must be treated differently.
    """

    # Find possible inlet/outlet link candidates as those attached to a node
    # of degree-1.
    poss_endlinks = []
    for nconn in nodes['conn']:
        if len(nconn) == 1:
            poss_endlinks.append(nconn[0])

    # Find the row(s)/column(s) corresponding to extent of the river at the
    # exit sides
    pixy, pixx = np.where(Iskel==True)
    e, w, n, s = np.max(pixx), np.min(pixx), np.min(pixy), np.max(pixy)

    # Get row, column coordinates of all nodes endpoints
    n_r, n_c = np.unravel_index(nodes['idx'], Iskel.shape)

    # Find inlets and outlets by searching for nodes that intersect the first exit_side of
    # the image
    ins_outs = []
    for j in [0,1]:
        if exit_sides[j] == 'n':
            idcs = np.where(n_r==n)[0]
        elif exit_sides[j] == 's':
            idcs = np.where(n_r==s)[0]
        elif exit_sides[j] == 'e':
            idcs = np.where(n_c==e)[0]
        elif exit_sides[j] == 'w':
            idcs = np.where(n_c==w)[0]

        ins_outs.append([nodes['id'][i] for i in idcs])

    # If there were no inlet or outlet nodes found, take the possible inlet/outlet
    # that is closest to the corresponding exit side as the inlet/outlet node
    if len(ins_outs[0]) == 0:
        if exit_sides[0] == 'n':
            idcs = np.argmin(np.abs(n_r-n))
        elif exit_sides[0] == 's':
            idcs = np.argmin(np.abs(n_r-s))
        elif exit_sides[0] == 'e':
            idcs = np.argmin(np.abs(n_c-e))
        elif exit_sides[0] == 'w':
            idcs = np.argmin(np.abs(n_c-w))
        ins_outs[0] = [nodes['id'][idcs]]

    if len(ins_outs[1]) == 0:
        if exit_sides[1] == 'n':
            idcs = np.argmin(np.abs(n_r-n))
        elif exit_sides[1] == 's':
            idcs = np.argmin(np.abs(n_r-s))
        elif exit_sides[1] == 'e':
            idcs = np.argmin(np.abs(n_c-e))
        elif exit_sides[1] == 'w':
            idcs = np.argmin(np.abs(n_c-w))
        ins_outs[1] = [nodes['id'][idcs]]

    # Append inlets and outlets to nodes dictionary
    nodes['inlets'] = ins_outs[0]
    nodes['outlets'] = ins_outs[1]

    if len(nodes['inlets']) == 0:
        print('No inlet nodes found.')
    if len(nodes['outlets']) == 0:
        print('No outlet nodes found.')

    ## TODO: handle special cases where the link intersects the edge of the
    ## image but the node does not because the link is a loop. This might be
    ## "fixable" by adjusting the padding multiplier; I don't have any test
    ## cases to work on currently so leaving this unimplemented for now.

    return nodes


def mask_to_centerline(Imask, es):
    """
    This function takes an input binary mask of a river and extracts its centerline.
    If there are multiple channels (and therefore islands) in the river, they
    will be filled before the centerline is computed.

    The input mask should have the following properties:
        1) There should be only one "blob" (connected component)
        2) Where the blob intersects the image edges, there should be only
           one channel. This avoids ambiguity in identifying inlet/outlet links

    INPUTS:
        Imask: the mask image (numpy array)
        es: two-character string comprinsed of "n", "e", "s", or "w". Exit sides
            correspond to the sides of the image that the river intersects.
            Upstream should be first, followed by downstream.
    OUTPUTS:
        dt.tif: geotiff of the distance transform of the binary mask
        skel.tif: geotiff of the skeletonized binary mask
        centerline.shp: shapefile of the centerline, arranged upstream to downstream
        cl.pkl: pickle file containing centerline coords, EPSG, and paths dictionary
    """

    # Lowercase the exit sides
    es = es.lower()

    # Keep only largest connected blob
    I = iu.largest_blobs(Imask, nlargest=1, action='keep')

    # Fill holes in mask
    Ihf = iu.fill_holes(I)

    # Skeletonize holes-filled river image
    Ihf_skel = m2g.skeletonize_river_mask(Ihf, es)

    # In some cases, skeleton spurs can prevent the creation of an endpoint
    # at the edge of the image. This next block of code tries to condition
    # the skeleton to prevent this from happening.
    # Find skeleton border pixels
    skel_rows, skel_cols = np.where(Ihf_skel)
    idcs_top = np.where(skel_rows==0)
    idcs_bottom = np.where(skel_rows==Ihf_skel.shape[0]-1)
    idcs_right = np.where(skel_cols==Ihf_skel.shape[1]-1)
    idcs_left = np.where(skel_cols==0)
    # Remove skeleton border pixels
    Ihf_skel[skel_rows[idcs_top], skel_cols[idcs_top]] = 0
    Ihf_skel[skel_rows[idcs_bottom], skel_cols[idcs_bottom]] = 0
    Ihf_skel[skel_rows[idcs_right], skel_cols[idcs_right]] = 0
    Ihf_skel[skel_rows[idcs_left], skel_cols[idcs_left]] = 0
    # Remove all pixels now disconnected from the main skeleton
    Ihf_skel = iu.largest_blobs(Ihf_skel, nlargest=1, action='keep')
    # Add the border pixels back
    Ihf_skel[skel_rows[idcs_top], skel_cols[idcs_top]] = 1
    Ihf_skel[skel_rows[idcs_bottom], skel_cols[idcs_bottom]] = 1
    Ihf_skel[skel_rows[idcs_right], skel_cols[idcs_right]] = 1
    Ihf_skel[skel_rows[idcs_left], skel_cols[idcs_left]] = 1

    # Keep only the largest connected skeleton
    Ihf_skel = iu.largest_blobs(Ihf_skel, nlargest=1, action='keep')

    # Convert skeleton to graph
    hf_links, hf_nodes = m2g.skel_to_graph(Ihf_skel)

    # Compute holes-filled distance transform
    Ihf_dist = distance_transform_edt(Ihf) # distance transform

    # Append link widths and lengths
    hf_links = lnu.link_widths_and_lengths(hf_links, Ihf_dist)

    """ Find shortest path between inlet/outlet centerline nodes"""
    # Put skeleton into networkX graph object
    G = nx.Graph()
    G.add_nodes_from(hf_nodes['id'])
    for lc, wt in zip(hf_links['conn'], hf_links['len']):
        G.add_edge(lc[0], lc[1], weight=wt)

    # Get endpoints of graph
    endpoints = [nid for nid, nconn in zip(hf_nodes['id'], hf_nodes['conn']) if len(nconn) == 1]

    # Filter endpoints if we have too many--shortest path compute time scales as a power of len(endpoints)
    while len(endpoints) > 100:
        ep_r, ep_c = np.unravel_index([hf_nodes['idx'][hf_nodes['id'].index(ep)] for ep in endpoints], Ihf_skel.shape)
        pct = 10
        ep_keep = set()
        for esi in [0,1]:
            if es[esi] == 'n':
                n_pct = int(np.percentile(ep_r, pct))
                ep_keep.update(np.where(ep_r <= n_pct)[0])
            elif es[esi] == 's':
                s_pct = int(np.percentile(ep_r, 100-pct))
                ep_keep.update(np.where(ep_r >= s_pct)[0])
            elif es[esi] == 'e':
                e_pct = int(np.percentile(ep_c, 100-pct))
                ep_keep.update(np.where(ep_c > e_pct)[0])
            elif es[esi] == 'w':
                w_pct = int(np.percentile(ep_c, pct))
                ep_keep.update(np.where(ep_c < w_pct)[0])

        endpoints = [endpoints[ek] for ek in ep_keep]

    # Get all paths from inlet(s) to outlets
    longest_shortest_paths = []
    for inl in endpoints:
        temp_lens = []
        for o in endpoints:
            temp_lens.append(nx.dijkstra_path_length(G, inl, o, weight='weight'))
        longest_shortest_paths.append(max(temp_lens))

    # The two end nodes with the longest shortest path are the centerline's endnodes
    end_nodes_idx = np.where(np.isclose(np.max(longest_shortest_paths),longest_shortest_paths))[0]
    end_nodes = [endpoints[i] for i in end_nodes_idx]

    # It is possible that more than two endnodes were identified; in these cases,
    # choose the nodes that are farthest apart in Euclidean space
    en_r, en_c = np.unravel_index([hf_nodes['idx'][hf_nodes['id'].index(en)] for en in end_nodes], Ihf_skel.shape)
    ep_coords = np.r_['1,2,0', en_r, en_c]
    ep_dists = cdist(ep_coords, ep_coords, 'euclidean')
    en_idcs_to_use = np.unravel_index(np.argmax(ep_dists), ep_dists.shape)
    end_nodes = [end_nodes[eitu] for eitu in en_idcs_to_use]

    # Ensure that exactly two end nodes are identified
    if len(end_nodes) != 2:
        raise RuntimeError('{} endpoints were found for the centerline. (Need exactly two).'.format(len(end_nodes)))

    # Find upstream node
    en_r, en_c = np.unravel_index([hf_nodes['idx'][hf_nodes['id'].index(n)] for n in end_nodes], Ihf_skel.shape)

    # Compute error for each end node given the exit sides
    errors = []
    for orientation in [0,1]:
        if orientation == 0:
            er = en_r
            ec = en_c
        elif orientation == 1:
            er = en_r[::-1]
            ec = en_c[::-1]

        err = 0
        for ot in [0,1]:
            if es[ot].lower() == 'n':
                err = err + er[ot]
            elif es[ot].lower() == 's':
                err = err + Ihf_dist.shape[0] - er[ot]
            elif es[ot].lower() == 'w':
                err = err + ec[ot]
            elif es[ot].lower() == 'e':
                err = err + Ihf_dist.shape[1] - ec[ot]
        errors.append(err)
    # Flip end node orientation to get US->DS arrangement
    if errors[0] > errors[1]:
        end_nodes = end_nodes[::-1]

    # Create centerline from links along shortest path
    nodespath = nx.dijkstra_path(G, end_nodes[0], end_nodes[1]) # nodes shortest path
    # Find the links along the shortest node path
    cl_link_ids = []
    for u,v in zip(nodespath[0:-1], nodespath[1:]):
        ulinks = hf_nodes['conn'][hf_nodes['id'].index(u)]
        vlinks = hf_nodes['conn'][hf_nodes['id'].index(v)]
        cl_link_ids.append([ul for ul in ulinks if ul in vlinks][0])

    # Create a shortest-path links dict
    cl_links = dict.fromkeys(hf_links.keys())
    dokeys = list(hf_links.keys())
    dokeys.remove('n_networks') # Don't need n_networks
    for clid in cl_link_ids:
        for k in dokeys:
            if cl_links[k] is None:
                cl_links[k] = []
            cl_links[k].append(hf_links[k][hf_links['id'].index(clid)])

    # Save centerline as shapefile
#    lnu.links_to_shapefile(cl_links, igd, rmh.get_EPSG(paths['skel']), paths['cl_temp_shp'])

    # Get and save coordinates of centerline
    cl = []
    for ic, cll in enumerate(cl_link_ids):
        if ic == 0:
            if hf_links['idx'][hf_links['id'].index(cll)][0] != hf_nodes['idx'][hf_nodes['id'].index(end_nodes[0])]:
                hf_links['idx'][hf_links['id'].index(cll)] = hf_links['idx'][hf_links['id'].index(cll)][::-1]
        else:
            if hf_links['idx'][hf_links['id'].index(cll)][0] != cl[-1]:
                hf_links['idx'][hf_links['id'].index(cll)] = hf_links['idx'][hf_links['id'].index(cll)][::-1]

        cl.extend(hf_links['idx'][hf_links['id'].index(cll)][:])

    # Uniquify points, preserving order
    cl = list(OrderedSet(cl))

    # Convert back to coordinates
    cly, clx = np.unravel_index(cl, Ihf_skel.shape)

    # Get width at each pixel of centerline
    pix_width = [Ihf_dist[y,x]*2 for x,y in zip(clx,cly)]

    coords = np.transpose(np.vstack((clx,cly)))

    return coords, pix_width


def resample_line(xs, ys, npts=None, nknots=None, k=3):
    """
    Resamples a line defined by x,y coordinates such that coordinates are
    evenly-spaced.
    If the optional npts parameter is not specified, the
    line will be resampled with the same number of points as the input
    coordinates.
    k refers to order of spline that is fit to coordinates for resampling.
    """

    # FitPack in si.splprep can't handle duplicate points in the line, so remove them
    no_dupes = np.array(np.where(np.abs(np.diff(xs)) + np.abs(np.diff(ys)) > 0)[0], dtype=np.int)
    xs_nd = np.r_[xs[no_dupes], xs[-1]]
    ys_nd = np.r_[ys[no_dupes], ys[-1]]

    # Get indices of knots
    if nknots is None:
        nknots = len(xs_nd)
    knot_indices = np.linspace(0, len(xs_nd), nknots, dtype=np.int)
    knot_indices[knot_indices > len(xs_nd) - 1] = len(xs_nd) - 1
    knot_indices = np.unique(knot_indices)

    # Create spline
    spline, _ = si.splprep([xs_nd[knot_indices], ys_nd[knot_indices]], k=k)

    # Evaluate spline
    if npts is None:
        npts = len(xs)
    t = np.linspace(0, 1, npts)
    resampled_coords = si.splev(t, spline)

    return resampled_coords, spline


def evenly_space_line(xs, ys, npts=None, k=3, s=0):
    """
    Resamples a curve defined by x,y coordinates such that coordinates are
    evenly-spaced.
    If the optional npts parameter is not specified, the
    line will be resampled with the same number of points as the input
    coordinates.
    k refers to order of spline that is fit to coordinates for resampling.
    xs, ys must be numpy arrays.
    """

    # FitPack in si.splprep can't handle duplicate points in the line, so remove them
    shapely_line = LineString(zip(xs, ys))
    shapely_line = shapely_line.simplify(0)
    xs_nd, ys_nd = np.array(shapely_line.coords.xy[0]), np.array(shapely_line.coords.xy[1])

    # Create spline
    spline, _ = si.splprep([xs_nd, ys_nd], k=k, s=s)

    # Evaluate spline
    if npts is None:
        npts = len(xs)
    t = np.linspace(0, 1, npts)
    resampled_coords = si.splev(t, spline)

    return resampled_coords, spline


def offset_linestring(linestring, distance, side):

    """
    INPUTS:
        linestring: shapely linestring
        distance: distance to offset linestring
        side: 'left' or 'right' to specify which side to compute offset
    """

    # Perform the offset
    offset = linestring.parallel_offset(distance, side)

    # Ensure that offset is not a MultiLineString by deleting all but the longest linestring
    if type(offset) is shapely.geometry.multilinestring.MultiLineString:
        ls_lengths = [ls.length for ls in offset]
        offset = offset[ls_lengths.index(max(ls_lengths))]
        print('Multilinestring returned in offset_linestring; clipped to longest but check output.')

    # Ensure offset linestring is oriented the same as the input linestring
    xy_orig_start = (linestring.coords.xy[0][0], linestring.coords.xy[1][0])

    # Get endpoint coordinates of offset linestring
    xy_offset_start = (offset.coords.xy[0][0], offset.coords.xy[1][0])
    xy_offset_end = (offset.coords.xy[0][-1], offset.coords.xy[1][-1])

    if np.sum(np.sqrt((xy_orig_start[0]-xy_offset_start[0])**2 + (xy_orig_start[1]-xy_offset_start[1])**2)) > np.sum(np.sqrt((xy_orig_start[0]-xy_offset_end[0])**2 + (xy_orig_start[1]-xy_offset_end[1])**2)):
        offset = LineString(offset.coords[::-1])

    return offset


def inflection_pts_oversmooth(xs, ys, n_infs):
    """
    Computes inflection points as the intersection of a line given by
    xs, ys and its (over)smoothed version.
    INPUTS:
        xs: np-type array of x-coordinates
        ys: np-type array of y-coordinates
        n_infs: approx how many inflection points are expected? This sets the
                degree of smoothing. For meandering rivers, n_infs can be
                approximated by the relationship: wavelength = 10W, for average
                width W.
    """
    def generate_smoothing_windows(start, stop, nbreaks, polyorder=3):
        """
        Generate an array of window sizes for smoothing. Each value must be
        greater than the polyorder of the smoother and be odd.
        """

        smoothwins = np.linspace(start, stop, nbreaks, dtype=np.int)

        # Window must be greater than polyorder
        smoothwins[smoothwins<polyorder] = polyorder + 1
        # Window must be odd
        smoothwins[smoothwins % 2 == 0] = smoothwins[smoothwins % 2 == 0] + 1
        smoothwins = np.unique(smoothwins)

        return smoothwins


    def smoothing_iterator(xs, ys, smoothing_windows, n_infs, polyorder):

        for i, sw in enumerate(smoothing_windows):

            # Smooth the line's coordinates
            xs_sm = signal.savgol_filter(xs, window_length=sw, polyorder=polyorder, mode='interp')
            ys_sm = signal.savgol_filter(ys, window_length=sw, polyorder=polyorder, mode='interp')

            # Conver to shapely objects for intersection detection
            ls = LineString([(x,y) for x,y in zip(xs, ys)])
            ls_sm = LineString([(x,y) for x,y in zip(xs_sm, ys_sm)])

            intersects = ls.intersection(ls_sm)
            if type(intersects) is shapely.geometry.point.Point: # Points have no length so check
                n_ints = 1
            else:
                n_ints = len(ls.intersection(ls_sm))

            if n_ints < n_infs:
                break

        return smoothing_windows[i-1], smoothing_windows[i]

    # Set polyorder for smoother; could be included as a parameter
    polyorder = 3

    # Find the smoothing window that provides a number of intersections closest
    # to the provided n_infs
    prepost_tolerance = 10 # difference between smoothing window sizes to stop iterating
    prev = 0 # initial minimum smoothing window size
    post = len(xs)/5 # initial maximum smoothing window size
    while post - prev > prepost_tolerance:
        s_windows = generate_smoothing_windows(prev, post, 25, polyorder=3)
        prev, post = smoothing_iterator(xs, ys, s_windows, n_infs, polyorder)

        ## TODO: should add a counter/tracker to ensure n_infs can actually be
        # obtained and avoid an infinite loop

    # Use the optimized smoothing window to smooth the signal
    window = post
    xs_sm = signal.savgol_filter(xs, window_length=window, polyorder=polyorder, mode='interp')
    ys_sm = signal.savgol_filter(ys, window_length=window, polyorder=polyorder, mode='interp')

    # Cast coordinates as shapely LineString objects
    ls = LineString([(x,y) for x,y in zip(xs, ys)])
    ls_sm = LineString([(x,y) for x,y in zip(xs_sm, ys_sm)])

    # Compute intersection points between original and oversmoothed centerline
    int_pts = ls.intersection(ls_sm)
    int_coords = np.array([(int_pts[i].coords.xy[0][0], int_pts[i].coords.xy[1][0]) for i in range(len(int_pts))])

    # Map the intersecting coordinates to the indices of the original signal
    idx = []
    for ic in int_coords:
        idx.append(np.argmin(np.sqrt((ic[0]-xs)**2+(ic[1]-ys)**2)))
    idx = np.sort(idx)

#    plt.close('all')
##    plt.plot(xs_sm,ys_sm)
#    plt.plot(xs,ys)
#    plt.plot(xs[idx], ys[idx], '.')
#    plt.axis('equal')

    # Package oversmoothed coordinates for export
    smoothline = np.array([xs_sm, ys_sm])

    return idx, smoothline


def centerline_mesh(coords, width_chan, meshwidth, grid_spacing, smoothing_param=1):
    """
    Generates a centerline mesh. Differs from valleyline_mesh() in that it draws
    perpendiculars rather than offsetting the valley line to compute mesh
    polygons. This method is more effective for narrower channels that don't
    require an exceptionally wide mesh (i.e. not much change).
    INPUTS:
        coords: 2xN list, tuple, np.array (xs, ys) of coordinates defining centerline
        width_chan: width of the river in same units of coords
        mesh_dist: how wide should the mesh be, in same units of coords
        grid_spacing: how far apart should mesh cells be, in same units of coords
    """
#    coords = ken.centerline
#    width_chan = ken.width_chans
#    meshwidth = ken.max_valley_width_pixels * ken.pixlen * 1.1
#    grid_spacing = meshwidth/10
#    smoothing_param = 1


    if np.shape(coords)[0] == 2 and np.size(coords) != 4:
        coords = np.transpose(coords)

    # Get lengths along centerline
    s, ds = s_ds(coords[:,0], coords[:,1])

    # Mirror centerline manually since scipy fucks it up - only flip the axis that has the largest displacement
    # Mirroring done to avoid edge effects when smoothing
    npad = int(width_chan / np.mean(ds) * 10) # Padding fixed at 10 channel widths
    xs_m, ys_m = mirror_line_ends(coords[:,0], coords[:,1], npad)

    # A smoothing filter of one-channel width will be passed over the centerline coordinates
    window_len = int(width_chan / np.mean(ds) * smoothing_param)
    if window_len % 2 == 0: # Window must be odd
        window_len = window_len + 1

    # Smooth
    xs_sm = signal.savgol_filter(xs_m, window_length=window_len, polyorder=3, mode='interp')
    ys_sm = signal.savgol_filter(ys_m, window_length=window_len, polyorder=3, mode='interp')

    plt.close('all')
    plt.plot(xs_sm, ys_sm)
    plt.plot(xs_m, ys_m)
    plt.axis('equal')

    # Re-sample centerline to even spacing
    s, _ = s_ds(xs_sm, ys_sm)
    npts = int(s[-1]/grid_spacing)
    xy_rs, _ = evenly_space_line(xs_sm, ys_sm, npts)
    xs_rs = xy_rs[0]
    ys_rs = xy_rs[1]

    # Get angles at each point along centerline
    C, A, s = curvars(xs_rs, ys_rs, unwrap=True)

    # Draw perpendiculars at each centerline point
    mesh_hwidth = meshwidth/2

    # Compute slope of perpendicular (w/ref to dx/dy and dy/dx)
    m_inv_xy = -1/(np.diff(xs_rs) / np.diff(ys_rs))
    m_inv_yx = -1/(np.diff(ys_rs) / np.diff(xs_rs))
    # For storing perpendicular points
    perps = []
    for ic in range(len(m_inv_xy)):

        # Compute perpendicular lines based on largest of dx, dy (reduces distortion)
        if m_inv_yx[ic] > m_inv_xy[ic]:
            dx = np.sqrt(mesh_hwidth**2/(1+m_inv_yx[ic]**2))
            dy = dx * m_inv_yx[ic]

        else:
            dy = np.sqrt(mesh_hwidth**2/(1+m_inv_xy[ic]**2))
            dx = dy * m_inv_xy[ic]

        upper_pt = (xs_rs[ic] + dx, ys_rs[ic] + dy)
        lower_pt = (xs_rs[ic] - dx, ys_rs[ic] - dy)

        perps.append((upper_pt, lower_pt))

    # Now orient perpendiculars so that both sides are continuous
    # NOTE: this method is not guaranteed to work when the grid spacing is much
    # larger than the buffer width (it likely will be fine, but for highly-
    # curved bends failure is possible). There are more robust ways to separate
    # points into left/right bank, but this is quick, dirty, and works for most
    # applications.
    perp_aligned = [perps[0]]
    for ip in range(1,len(perps)):

        left_pre, right_pre = perp_aligned[ip-1]

        p0 = perps[ip][0]
        p1 = perps[ip][1]

        if np.sqrt((p0[0]-left_pre[0])**2 + (p0[1]-left_pre[1])**2) <  np.sqrt((p1[0]-left_pre[0])**2 + (p1[1]-left_pre[1])**2):
            perp_aligned.append((p0, p1))
        else:
            perp_aligned.append((p1, p0))

    plt.close('all')
    plt.plot(xs_rs, ys_rs,'.')
    plt.axis('equal')
    for p in perp_aligned:
        plt.plot(p[0][0], p[0][1], 'k.')
        plt.plot(p[1][0], p[1][1], 'r.')


    # Trim the centerline to remove the mirrored portions
    start_idx = np.argmin(np.sqrt((coords[0,0]-xs_rs)**2+(coords[0,1]-ys_rs)**2)) - 1
    end_idx = np.argmin(np.sqrt((coords[-1,0]-xs_rs)**2+(coords[-1,1]-ys_rs)**2)) + 1

    # Build the polygon mesh
    polys = []
    for i in range(start_idx, end_idx+1):
        polys.append([perp_aligned[i][0], perp_aligned[i][1], perp_aligned[i+1][1], perp_aligned[i+1][0], perp_aligned[i][0]])


    perps_out = perp_aligned[start_idx:end_idx+1]
    cl_resampled = np.r_['1,2,0', xs_rs, ys_rs]
    s_out = s[start_idx:end_idx+1] - s[start_idx]

    return perps_out, polys, cl_resampled, s_out



def mirror_line_ends(xs, ys, npad):
    """
    Reflects both ends of a line defined by x and y coordinates. The mirrored
    distance is set by npad, which refers to the number of vertices along the
    line to mirror.
    """

    # Mirror the beginning of the line
    diff_x = np.diff(xs[0:npad])
    xs_m = np.concatenate((np.flipud(xs[1] - np.cumsum(diff_x)), xs))
    diff_y = np.diff(ys[0:npad])
    ys_m = np.concatenate((np.flipud(ys[1] - np.cumsum(diff_y)), ys))

    # Mirror the end of the line
    diff_x = np.diff(xs[-npad:][::-1])
    xs_m = np.concatenate((xs_m, xs_m[-1] - np.cumsum(diff_x)))
    diff_y = np.diff(ys[-npad:][::-1])
    ys_m = np.concatenate((ys_m, ys_m[-1] - np.cumsum(diff_y)))

    return xs_m, ys_m


def valleyline_mesh(coords, width_chan, bufferdist, grid_spacing, smoothing=0.15):
    """
    This function generates a mesh over an input river centerline. The mesh
    is generated across the valley, not just the channel width, in order to
    perform larger-scale spatial analyses. With the correct parameter
    combinations, it can also be used to generate a mesh for smaller-scale
    analysis, but it is optimized for larger and strange behavior may occur.

    Many plotting commands are commented out throughout this script as it's
    still somewhat in beta mode.

    INPUTS:
        coords: Nx2 list, tuple, or np.array of x,y coordinates. Coordinates MUST be in projected CRS for viable results.
        width_chan: estimated width. Units MUST correspond to those of the input coordinates
        bufferdist: distance between centerline and left or right bufferline, in units of coords
        grid_spacing: fraction of input centerline length that should be used for smoothing to create the valley centerline  (between 0 and 1)
        smoothing: fraction of centerline length to use for smoothing window

    OUTPUTS:
        lines - the "perpendiculars" to the centerline used to generate the mesh
        polys - coordinates of the polygons representing the grid cells of the mesh
    """
    
    def find_cl_intersection_pts_and_distance(endpts, cl):
        """
        Given a list of transect endpoints, this computes the intersection
        point along the centerline, and then returns the corresponding
        along-centerline distance to that point from the upstream boundary.
        
        End transects might not intersect the centerline. In these cases,
        we rely on the previous processing steps that artificially extended 
        the centerline and simply drop the transects--effectively clipping
        the centerline to the first and last transect intersections.
        """
        
        int_pts = []
        dist_to_int = []
        for ie, eps in enumerate(endpts):
            tsect = LineString(eps)
            int_pt = tsect.intersection(cl)
            
            if int_pt.coords == []: # There is no intersection
                int_pts.append(None)
                dist_to_int.append(None)
                continue
            
            # Project the intersection point to the centerline and return 
            # the along-centerline distance of this point
            dist_to_int.append(float(cl.project(int_pt)))
            
            int_pts.append(int_pt)
            
        dist_to_int = np.array(dist_to_int)
        int_pts = np.array(int_pts)
            
        # Now clip the distances, centerline, and endpoints where there were no intersections
        no_ints = dist_to_int==None
        dist_to_int = dist_to_int[~no_ints]
        int_pts = int_pts[~no_ints]
        cl_clip = LineString(zip(np.array(cl.coords.xy[0])[~no_ints], np.array(cl.coords.xy[1])[~no_ints]))
        ep_clip = [ep for iep, ep in enumerate(endpts) if no_ints[iep]==False]        
       
        # Reset the origin
        dist_to_int = dist_to_int - dist_to_int[0]
        
            
        return dist_to_int, cl_clip, ep_clip
            
    
    def iterative_cl_pt_mapping(cl, bufdists, side):
        from fastdtw import fastdtw
        from scipy.spatial.distance import euclidean

        mapper = []
        lines = []
        plt.close()
     
        old = cl
        for i, bd in enumerate(bufdists):

            new = shapely_offset_ls(cl, bd, side)
            
            Co, Ao, so = curvars(old.coords.xy[0], old.coords.xy[1])
            Cn, An, sn = curvars(new.coords.xy[0], new.coords.xy[1])
            
            Ao = np.insert(Ao, 0, 0)
            An = np.insert(An, 0, 0)
            
            distance, path = fastdtw(Ao, An, dist=euclidean)
            path = np.array(path)
            
            mapper.append(path)
            lines.append(new)
            
            old = new
   
        return lines, mapper
        
    
    def get_transect_indices_along_buffered_lines(cl, mapper):
        """
        Returns a map of the index of each offset line mapped from the 
        original centerline. Keys are original centerline indices; values
        are lists the length of number of offsets (i.e. length of bufdists). 
        Really only the last entry in each value is needed, but keeping them
        all for developing/debugging purposes.
        """
        pts = {}
        for i in range(len(cl.coords.xy[0])):
            
            idx = i
            idxlist = [idx]

            for m in mapper:
                m = np.array(m)
                m_idx = (np.where(m[:,0]==idx)) # Get the most-downstrea
                if len(m_idx) > 1:
                    print(m_idx)
                m_idx = np.max(m_idx) # Chooses the most downstream if multiple are available
                idx = m[m_idx,1]
                idxlist.append(idx)
            pts[i] = idxlist
    
        return pts
    
    
    def get_transect_endpoints_xy(lpts, rpts):
        """
        Given dictionaries that map centerline points to indices along buffered
        left and right lines, this returns the endpoints of each transect.
        """
        assert len(lpts) == len(rpts)
        
        endpoints = []
        for i in range(len(lpts)):
            lidx = lpts[i][-1]
            ridx = rpts[i][-1]
            
            lxy = (llines[-1].coords.xy[0][lidx], llines[-1].coords.xy[1][lidx])
            rxy = (rlines[-1].coords.xy[0][ridx], rlines[-1].coords.xy[1][ridx])
            endpoints.append([lxy, rxy])
            
        return endpoints
    
    
    def shapely_offset_ls(ls, dist, side):
        """
        Just a wrapper around shapely's offset_linestring() function. That 
        function adds little barbs sometimes to the end of the offset
        linestring. This function detects and removes those.
        """
        offset = offset_linestring(ls, dist, side)
        
        # Look for barbs by finding abrupt angle changes
        _, A, _ = curvars(offset.coords.xy[0], offset.coords.xy[1])
        possibles = np.where(np.abs(np.diff(A)) > 1.5)[0] # Threshold set at 1.5 radians
        
        if len(possibles) == 0:
            return offset
        else:
            st_idx = 0
            en_idx = len(offset.coords) - 1
            for p in possibles:
                if p < len(offset.coords) / 2:
                    st_idx = max(st_idx, p+1)
                elif p > len(offset.coords) /2:
                    en_idx = min(en_idx, p)
            offset = LineString(offset.coords[st_idx:en_idx])
        
        # elif len(possibles) == 1: # Determine if it's the upstream or downstream that's barbed
        #     if possibles[0] > len(A)/2: # Downstream
        #         offset = LineString(zip(offset.coords.xy[0][:possibles[0]], offset.coords.xy[1][:possibles[0]]))
        #     else: # Upstream
        #         offset = LineString(zip(offset.coords.xy[0][possibles[0]:], offset.coords.xy[1][possibles[0]:]))
        # elif len(possibles) == 2:
        #     offset = LineString(zip(offset.coords.xy[0][possibles[0]:possibles[1]], offset.coords.xy[1][possibles[0]:possibles[1]]))
        # else:
        #     # import pdb; pdb.set_trace()
        #     raise Warning('Barbs could not be removed from centerline offset: dist={}, side={}.'.format(dist,side))
            
        return offset
    
    def mirror_lines(xs_o, ys_o, npad):
        # Mirror centerline manually since scipy fucks it up - only flip the axis that has the largest displacement
        # Mirroring done to avoid edge effects when smoothing
        
        xs_o2, ys_o2 = mirror_line_ends(xs_o, ys_o, npad)
        diff_x = np.diff(xs_o[0:npad])
        xs_o2 = np.concatenate((np.flipud(xs_o[1] - np.cumsum(diff_x)), xs_o))
        diff_y = np.diff(ys_o[0:npad])
        ys_o2 = np.concatenate((np.flipud(ys_o[1] - np.cumsum(diff_y)), ys_o))
    
        diff_x = np.diff(xs_o[-npad:][::-1])
        xs_o2 = np.concatenate((xs_o2, xs_o2[-1] - np.cumsum(diff_x)))
        diff_y = np.diff(ys_o[-npad:][::-1])
        ys_o2 = np.concatenate((ys_o2, ys_o2[-1] - np.cumsum(diff_y)))
        
        return(xs_o2, ys_o2)


    """ Function code begins here """
    # coords = process_river.centerline
    # width_chan = process_river.width_chans
    # bufferdist = 0.5 #process_river.max_valley_width_pixels * process_river.pixlen * 1.1
    # grid_spacing = 0.25# np.percentile(process_river.links['len'],25)
    # smoothing = 0.5

    if np.shape(coords)[0] == 2 and np.size(coords) != 4:
        coords = np.transpose(coords)

    # Separate coordinates into xs and ys (o indicates original coordinates)
    xs_o = coords[:,0]
    ys_o = coords[:,1]
    
    # Set smoothing window size based on smoothing parameter and centerline length
    s, ds = s_ds(xs_o, ys_o)
    window_len = int(smoothing * s[-1] / np.mean(ds))
    window_len = int(min(len(xs_o)/5, window_len)) # Smoothing window cannot be longer than 1/5 the centerline
    if window_len % 2 == 0: # Window must be odd
        window_len = window_len + 1

    # Extend the centerline ends to avoid boundary effects; we'll clip them later    
    xs_o2, ys_o2 = mirror_lines(xs_o, ys_o, window_len)
    
    # Smooth the coordinates before buffering
    xs_sm = signal.savgol_filter(xs_o2, window_length=window_len, polyorder=3, mode='interp')
    ys_sm = signal.savgol_filter(ys_o2, window_length=window_len, polyorder=3, mode='interp')

    # Create shapely LineString centerline
    cl = LineString([(x,y) for x, y in zip(xs_sm, ys_sm)])
    
    # Simplify the linestring
    npts = int(cl.length/width_chan/4)
    tol = width_chan/100
    while True:
        cl2 = cl.simplify(tol)
        if len(cl2.coords) > npts:
            tol = tol * 1.1
        else:
            break

    # Offset valley centerline for left and right valleylines    
    bdists = np.linspace(0, bufferdist, min(int(bufferdist/width_chan/10), 25))   
    bdists = bdists[1:]

    # Iteratively create offset lines and map each centerline index    
    llines, lmap = iterative_cl_pt_mapping(cl2, bdists, 'left')
    rlines, rmap = iterative_cl_pt_mapping(cl2, bdists, 'right')
    
    lpts = get_transect_indices_along_buffered_lines(cl2, lmap)
    rpts = get_transect_indices_along_buffered_lines(cl2, rmap)
    
    endpts = get_transect_endpoints_xy(lpts, rpts)
    
    dists, cl_clip, ep_clip = find_cl_intersection_pts_and_distance(endpts, cl2)
    dists = np.array([float(d) for d in dists]) # avoid dtype('O') error in numpy.interp
    
    # Now build the interpolating functions
    dists_to_interpolate = np.linspace(0, np.max(dists), 200)
    xp_l = np.array([ep[0][0] for ep in ep_clip])
    yp_l = np.array([ep[0][1] for ep in ep_clip])
    xp_r = np.array([ep[1][0] for ep in ep_clip])
    yp_r = np.array([ep[1][1] for ep in ep_clip])
    
    # Interpolate
    x_left = np.interp(dists_to_interpolate, dists, xp_l)
    y_left = np.interp(dists_to_interpolate, dists, yp_l)
    x_right = np.interp(dists_to_interpolate, dists, xp_r)
    y_right = np.interp(dists_to_interpolate, dists, yp_r)
    
    # # Plot the grid
    # plt.close('all')
    # plt.plot(cl.coords.xy[0], cl.coords.xy[1], '--k')
    # plt.axis('equal')
    # for xl, yl, xr, yr in zip(x_left, y_left, x_right, y_right):
    #     plt.plot((xr, xl), (yr,yl))


    # Mesh is generated; export transects and polygons as shapely geometries
    transects = []
    for xl, yl, xr, yr in zip(x_left, y_left, x_right, y_right):
        transects.append(((xl, yl), (xr, yr)))
        
    # The centerline was elongated to avoid boundary effects, so now we can 
    # clip the transects to only those that are needed
    cl_orig = LineString(zip(xs_o, ys_o))
    intersects_cl = [LineString(t).intersects(cl_orig) for t in transects]
    first_idx = np.argmax(intersects_cl) - 1
    last_idx = len(intersects_cl) - np.argmax(intersects_cl[::-1]) - 1 + 1 # -1/+1 for explicitness    
    transects = [transects[i] for i in range(first_idx, last_idx + 1)]

    # Create mesh polygons
    polys = []
    for i in range(len(transects)-1):
        polys.append(Polygon([transects[i][0], transects[i][1], 
                     transects[i+1][1], transects[i+1][0],
                     transects[i][0]]))
        
    # Convert transects to shapely objects
    transects = [LineString(t) for t in transects]
    
    # Clip the smooth centerline for return
    xs_sm = xs_sm[window_len-1:(len(xs_sm)-window_len+1)]
    ys_sm = ys_sm[window_len-1:(len(ys_sm)-window_len+1)]
    cl_smooth = LineString(zip(xs_sm, ys_sm))
        
    return transects, polys, cl_smooth


def chan_width(coords, Imask, pixarea=1):

    """
    Returns two estimates of channel width: width_channels is the average
    width of just the channels, and width_extent is the average width of the
    extent of the river (includes islands).

    Inputs:
        coords - Nx2 list of (x,y) coordinates defining the centerline of the input mask
        Imask - binary mask on which the centerline was computed
        pixarea - (Optional, float) area of each pixel in the mask. If none is
                  provided, widths will be in units of pixels.
    """

    # Estimate length from coodinates
    s, _ = s_ds(coords[0], coords[1])
    len_est = s[-1]

    # Estimate unfilled channel width (average widths of actual channels)
    Imask = iu.largest_blobs(Imask, nlargest=1, action='keep')
    area_est = np.sum(np.array(Imask, dtype=np.bool)) * pixarea
    width_channels = area_est/len_est

    # Estimate filled channel width (average width of entire channel extents i.e. including islands)
    Imask = iu.fill_holes(Imask)
    area_est = np.sum(np.array(Imask, dtype=np.bool)) * pixarea
    width_extent = area_est/len_est

    return width_channels, width_extent


def curvars(xs, ys, unwrap=True):

    """
    Compute curvature (and intermediate variables) for a given set of x,y
    coordinates.
    """
    xs = np.array(xs)
    ys = np.array(ys)

    xAi0  = xs[:-1]
    xAi1 = xs[1:]
    yAi0 = ys[:-1]
    yAi1 = ys[1:]

    # Compute angles between x,y nodes
#    A = np.arctan(np.divide(yAi1-yAi0,xAi1-xAi0))
    A = np.arctan2(yAi1-yAi0,xAi1-xAi0)

    if unwrap is not True:
        Acopy = A.copy()

    # Fix phase jumps in angle larger than pi
    A = np.unwrap(A)

    # Compute distance and cumulative distance between nodes
    s, ds = s_ds(xs, ys)
    s = np.delete(s, 0)

    # Compute curvature via central differencing
    # See: http://terpconnect.umd.edu/~toh/spectrum/Differentiation.html
    sd = np.zeros([len(s)])
    sd[0] = s[2]
    sd[1:] = s[0:-1]

    su = np.zeros([len(s)])
    su[0:-1] = s[1:]
    su[-1] = s[-3]

    Ad = np.zeros([len(s)])
    Ad[0] = A[2]
    Ad[1:] = A[0:-1]

    Au = np.zeros([len(s)])
    Au[0:-1] = A[1:]
    Au[-1] = A[-3]

    # Curvatures - checked against Matlab implementation, OK
    C = -np.divide((np.divide(Au-A,su-s)*(s-sd)+np.divide(A-Ad,s-sd)*(su-s)),(su-sd))

    if unwrap is not True:
        Areturn = Acopy
    else:
        Areturn = A

    return C, Areturn, s


def s_ds(xs, ys):

    ds = np.sqrt((np.diff(xs))**2+(np.diff(ys))**2)
    s = np.insert(np.cumsum(ds), 0, 0)

    return s, ds


def smooth_curvatures(C, cvtarget, tolerance=10):
    """
    Smoothes a curvature signal until the coefficient of variation of its differenced
    curvatures is within tolerance percent.
    """
    from scipy.stats import variation

    smoothstep = int(len(C)/100)
    window = smoothstep
    if window % 2 == 0: # Window must be odd
        window = window + 1

    cv = 10000
    while abs((cv-cvtarget)/cv*100) > tolerance:
        Cs = signal.savgol_filter(C, window_length=window, polyorder=3, mode='interp')
        cv = variation(np.diff(Cs))

        window = window + smoothstep
        if window % 2 == 0: # Window must be odd
            window = window + 1

        if window > len(C)/2:
            print('Could not find solution.')
            return Cs

    return Cs


def inflection_points(C):
    """
    Returns the inflection points for an input curvature signal.
    """
    infs1 = np.where(np.logical_and(C[1:] > 0, C[:-1] < 0))[0] + 1
    infs2 = np.where(np.logical_and(C[1:] < 0, C[:-1] > 0))[0] + 1
    infs = np.sort(np.concatenate((infs1, infs2)))

    return infs


class centerline():

        def __init__(self, x, y, attribs=None):
            """
            attribs is a dictionary with attributes; can be single values like
            average channel width or one value per coordinate like local width.
            """
            # Store original coordinates
            self.xo = x
            self.yo = y

            # Store attributes
            if attribs:
                for a in attribs.keys():

                    try:
                        alen = len(attribs[a])
                    except:
                        alen = 1

                    if alen == 1 or alen == len(x):
                        setattr(self, a, attribs[a])
                    else:
                        print('Attribute () does not have the proper length and is not being stored.'.format(a))


        def __get_x_and_y(self):

            if hasattr(self, 'xrs'):
                x = self.xrs
                y = self.yrs
                vers = 'resampled'
            elif hasattr(self, 'xs'):
                x = self.xs
                y = self.ys
                vers = 'smooth'
            else:
                x = self.xo
                y = self.yo
                vers = 'original'

            return x, y, vers


        def smooth(self, window=None, n=1, k=3, x=None, y=None):
            """
            Smooths the x and y coordinates of the centerline using a k-th order
            Savitzky-Golay filter.

            window refers to the number of points to use in the moving window; must be odd
            n is the number of times to perform the smoothing.
            """
            if x is None:
                x, y, _ = self.__get_x_and_y()

            if window is None:
                if hasattr(self, 'window_cl'):
                    window = self.window_cl
                else:
                    print('Must provide a smoothing window.')
                    return

            # Ensure window is integer and odd
            window = int(window)
            if window % 2 == 0:
                window = window + 1

            self.xs = signal.savgol_filter(x, window_length=window, polyorder=k, mode='interp')
            self.ys = signal.savgol_filter(y, window_length=window, polyorder=k, mode='interp')

            # Could make this recursive but if a non-default x,y are passed in, it would not function as expected
            if n > 1:
                for i in range(1,n-1):
                    self.xs = signal.savgol_filter(self.xs, window_length=window, polyorder=3, mode='interp')
                    self.ys = signal.savgol_filter(self.ys, window_length=window, polyorder=3, mode='interp')



        def resample(self, N, x=None, y=None):
            """
            If no arguments are provided for x and y, will resample the smoothed
            coordinates if available, else will resample the original coordinates.

            N is the number of points that the resulting centerline
            should contain.
            """
            if x is None:
                x, y, _ = self.__get_x_and_y()

            xy, spline = evenly_space_line(x, y, npts=N)
            self.xrs = xy[0]
            self.yrs  = xy[1]


        def s(self, x=None, y=None):

            if x is None:
                x, y, _ = self.__get_x_and_y()

            sss, _ = s_ds(x,y)
            return sss


        def ds(self, x=None, y=None):

            if x is None:
                x, y, _ = self.__get_x_and_y()

            _, dss = s_ds(x,y)
            return dss


        def C(self, x=None, y=None):
            """
            Important: curvatures are negativized to match the zs approach
            """
            if x is None:
                x, y, _ = self.__get_x_and_y()

            Cs, _, _ = curvars(x, y, unwrap=True)
            Cs = np.insert(Cs, 0, 0)
            return -Cs


        def Csmooth(self, window=None, x=None, y=None):

            if window is None:
                if hasattr(self, 'window_C'):
                    window = self.window_C
                else:
                    print('Must provide a smoothing window.')
                    return

            Cs = self.C()
            Cs = signal.savgol_filter(Cs, window_length=window, polyorder=3, mode='interp')
#            Cs = signal.medfilt(Cs,kernel_size=5)
            return Cs


        def infs(self, N, x=None, y=None):
            """
            Finds inflection points.

            N is the number of expected inflection points. It can be estimated
            from N ~= centerline length / 10W, but visual inspection is usually
            best.
            """

            if x is None:
                x, y, _ = self.__get_x_and_y()

            # Use centerline oversmoothing to find inflection points
            self.infs_os, _ = inflection_pts_oversmooth(x, y, n_infs=N)


        def infsC(self, x=None, y=None):

            if not hasattr(self, 'C'):
                self.curvature()

            # Use curvature to find inflection points
            self.infs_C = inflection_points(self.C)


        def intersection_points(self, x2, y2, x1=None, y1=None):

            if x1 is None:
                x1, y1, _ = self.__get_x_and_y()

            ls1 = LineString(zip(x1, y1))
            ls2 = LineString(zip(x2, y2))
            ls_intersections = ls1.intersection(ls2)
            self.ints_all = np.unique(np.sort([np.argmin(np.sqrt((x1-pt.coords.xy[0][0])**2 + (y1-pt.coords.xy[1][0])**2)) for pt in ls_intersections])) # locations of zero migration

            # Map the intersection points so that there is one point for every
            # pair of inflection points in inf_os
            # If there is only one intersection point, use it.
            # If none, use the first inflection point?
            # If multiple, use the one closest to the first inflection point
            if hasattr(self, 'infs_os'):

                s = self.s()

                # Compute the average bend length from the inflection points
                ints = []
                s = self.s()
                abl = (s[self.infs_os[-1]] - s[self.infs_os[0]])/(len(self.infs_os)-1)

                for i in range(len(self.infs_os)):

                    i0 = self.infs_os[i]

                    # Find nearest intersection point for first inflection
                    if i == 0:
                        intidx = np.argmin(np.abs(s[i0] - s[self.ints_all]))
                        ints.append(self.ints_all[intidx])

                    # Else find the nearest interesection point that is downstream of the bend's first inflection point
                    else:
                        possible_ints = self.ints_all[self.ints_all>ints[i-1]]
                        dists = np.abs(s[possible_ints] - s[i0])
                        ints.append(possible_ints[np.argmin(dists)])

                    if i == len(self.infs_os)-1:
                        break

                self.ints = np.array(ints)

            else:
                print('Could not map intersections to inflection point pairs because infs_os not computed. Run infs() first.')


        def mig_rate_zs(self, x2, y2, dt_years, x1=None, y1=None, window=None):
            """
            Compute migration rate using Sylvester et al's method of
            dynamic time warping.
            """
            if x1 is None:
                x1, y1, _ = self.__get_x_and_y()

            if window is None:
                if hasattr(self, 'window_C'):
                    window = self.window_C
                else:
                    window = 5 # must be greater than the polyorder, which is 3 by default

            import os
            import sys
            script_dir = r"C:\Users\Jon\Desktop\Research\Koyukukon\Normalize migration rates\Code\curvaturepy-master"
            sys.path.append(os.path.abspath(script_dir))
            import cline_analysis as ca

            self.mr_zs, self.mrs_zs, self.p_zs, self.q_zs = ca.get_migr_rate(x1, x2, y1, y2, dt_years, 0)

            # Smooth
            self.mr_zs_sm = signal.savgol_filter(self.mr_zs, window_length=window, polyorder=3, mode='interp')

            # Set cutoff-affected and erodibility-affected bends to NaN
            self.mr_zs_nan = self.mr_zs.copy()
            self.mr_zs_sm_nan = self.mr_zs_sm.copy()
            if hasattr(self, 'cut_ids'):
                for c in self.cut_ids:
                    self.mr_zs_nan[self.infs_os[c]:self.infs_os[c+1]] = np.NaN
                    self.mr_zs_sm_nan[self.infs_os[c]:self.infs_os[c+1]] = np.NaN

            if hasattr(self, 'erode_ids'):
                for e in self.erode_ids:
                    self.mr_zs_nan[self.infs_os[e]:self.infs_os[e+1]] = np.NaN
                    self.mr_zs_sm_nan[self.infs_os[e]:self.infs_os[e+1]] = np.NaN


        def plot(self, x=None, y=None):

            if x is None:
                x, y, version = self.__get_x_and_y()
            else:
                version = ''

            fig, ax = plt.subplots()
            legend = []
            ax.plot(x, y, 'k')
            legend.append(version + ' centerline')

            if hasattr(self, 'infs_os'):
                ax.plot(x[self.infs_os], y[self.infs_os], 'rs')
                legend.append('inflection points')

            if hasattr(self, 'ints_all'):
                ax.plot(x[self.ints_all],  y[self.ints_all], 'go')
                legend.append('intersection points')

            if hasattr(self, 'ints'):
                ax.plot(x[self.ints], y[self.ints], 'b^')
                legend.append('intersection points (mapped)')

            plt.legend(legend)
            plt.axis('equal')


        def zs_plot(self, window=None):
            """
            Copied verbatim from https://github.com/zsylvester/curvaturepy/blob/master/Purus_2_migration_rates.ipynb
            Slight modifications for meshing in the centerline class.
            """

            if hasattr(self, 'infs_os') is False:
                print('Must compute inflection points first.')
                return

            if hasattr(self, 'ints') is False:
                print('Must compute intersections first.')
                return

            if hasattr(self, 'mr_zs_nan') is False:
                print('Must compute migration rates first.')
                return
#            elif hasattr(self, 'mr_zs_sm_nan'):
#                migr_rate = self.mr_zs_sm_nan
            else:
                migr_rate = self.mr_zs_nan

            if hasattr(self, 'cut_ids') is False:
                cutoff_inds = []
            else:
                cutoff_inds = self.cut_ids

            if hasattr(self, 'erode_ids') is False:
                erodibility_inds = []
            else:
                erodibility_inds = self.erode_ids

            if window is None:
                if hasattr(self, 'window_C'):
                    window = self.window_C
                else:
                    print('Must provide a smoothing window.')
                    return

            LZC = self.infs_os
            LZM = self.ints
            s = self.s()
            curv = self.Csmooth()
            W = self.W

            fig, ax1 = plt.subplots(figsize=(18,4))
#            plt.tight_layout()

            y1 = 0.7
            y2 = 0.0
            y3 = -0.87
            y4 = -1.25

            for i in range(0,len(LZC)-1,2):
                xcoords = [s[LZC[i]],s[LZC[i+1]],s[LZC[i+1]],s[LZM[i+1]],s[LZM[i+1]],s[LZM[i]],s[LZM[i]],s[LZC[i]]]
                ycoords = [y1,y1,y2,y3,y4,y4,y3,y2]
                ax1.fill(xcoords,ycoords,color=[0.85,0.85,0.85],zorder=0)

            ax1.fill_between(s, 0, curv*W)
            ax2 = ax1.twinx()
            ax2.fill_between(s, 0, migr_rate, facecolor='green')

            ax1.plot([0,max(s)],[0,0],'k--')
            ax2.plot([0,max(s)],[0,0],'k--')

            ax1.set_ylim(y4,y1)
            ax2.set_ylim(-15,40)
            ax1.set_xlim(s[LZC[0]],s[-1])

            for i in erodibility_inds:
                xcoords = [s[LZC[i]],s[LZC[i+1]],s[LZC[i+1]],s[LZM[i+1]],s[LZM[i+1]],s[LZM[i]],s[LZM[i]],s[LZC[i]]]
                ycoords = [y1,y1,y2,y3,y4,y4,y3,y2]
                ax1.fill(xcoords,ycoords,color=[1.0,0.85,0.85],zorder=0)

            for i in cutoff_inds:
                xcoords = [s[LZC[i]],s[LZC[i+1]],s[LZC[i+1]],s[LZM[i+1]],s[LZM[i+1]],s[LZM[i]],s[LZM[i]],s[LZC[i]]]
                ycoords = [y1,y1,y2,y3,y4,y4,y3,y2]
                ax1.fill(xcoords,ycoords,color=[0.85,1.0,0.85],zorder=0)

            for i in range(len(LZC)-1):
                if np.sum(np.isnan(migr_rate[LZM[i]:LZM[i+1]]))>0:
                    xcoords = [s[LZC[i]],s[LZC[i+1]],s[LZC[i+1]],s[LZM[i+1]],s[LZM[i+1]],s[LZM[i]],s[LZM[i]],s[LZC[i]]]
                    ycoords = [y1,y1,y2,y3,y4,y4,y3,y2]
                    ax1.fill(xcoords,ycoords,color='w')

            for i in range(len(LZC)-1):
                if np.sum(np.isnan(migr_rate[LZM[i]:LZM[i+1]]))>0:
                    xcoords = [s[LZC[i]],s[LZC[i+1]],s[LZC[i+1]],s[LZM[i+1]],s[LZM[i+1]],s[LZM[i]],s[LZM[i]],s[LZC[i]]]
                    ycoords = [35,35,20.7145,0,-15,-15,0,20.7145]
                    ax2.fill(xcoords,ycoords,color='w')

            for i in range(0,len(LZC)-1,2):
                ax1.text(s[LZC[i]],0.5,str(i),fontsize=12)


def compute_eBI(path_meshlines, path_links, method='local'):
    """
    method can be 'local' or 'avg'
    """
    
    meshline_gdf = gpd.read_file(path_meshlines)
    links_gdf = gpd.read_file(path_links)

    if 'wid_adj' not in links_gdf.keys():
        raise RuntimeError('Widths have not been appended to links yet; cannot compute eBI.')

    inter = gpd.sjoin(meshline_gdf, links_gdf, op='intersects')

    # Conver link widths to floats
    widths = links_gdf.wid_adj.values
    widths = np.array([float(w) for w in widths])

    # Compute entropic braided index
    mesh_index = meshline_gdf.index.values
    eBI = [] # entropic braided index
    BI = [] # braided index
    for mi in mesh_index:
        # First see if the mesh intersects the centerlines
        try:
            int_links = np.array(inter['index_right'].values[inter.index.get_loc(mi)])
        except KeyError:
            eBI.append(0)
            BI.append(0)
            continue

        # A second check to handle strange cases
        bi_section = int_links.size
        if bi_section == 0:
            eBI.append(0)
            BI.append(0)
            continue

        # This is because numpy returns an array when multiple values returned, and an int when a single value is returned
        if int_links.size == 1:
            int_links = [int_links.tolist()]

        if method == 'avg':
            # Method 1: use the average link width
            ws = widths[int_links]

        elif method == 'local':

            # Method 2: use the local channel width
            ws = []
            for il in int_links:
                meshline = meshline_gdf.geometry.values[mi]
                rivline = links_gdf.geometry.values[il]
                int_pt = rivline.intersection(meshline)

                # If there are multiple intersection points along the same link, use the link's average width
                if type(int_pt) != shapely.geometry.point.Point:
                    ws.append(widths[il])
                else:
                    int_id = np.argmin(np.sqrt((np.array(rivline.coords.xy[0])-int_pt.coords.xy[0])**2+(np.array(rivline.coords.xy[1])-int_pt.coords.xy[1])**2))

                    # Converting from string to float
                    ws_il = links_gdf.wid_pix.values[il]
                    ws_il = np.array([float(w.replace(',','')) for w in ws_il.split(' ') if w != ''])
                    ws.append(ws_il[int_id])

        ws = np.array([w for w in ws if w > 0]) # Remove links of 0 width -- should determine why these are zero, probably due to computing widths on the original mask instead of the pre-processed one...
        probs = ws / np.sum(ws)
        if any(probs == 0):
            break
        H = -np.sum(probs*np.log2(probs))
        ebi_section = 2**H
        eBI.append(ebi_section)
        BI.append(bi_section)

    return np.array(eBI), np.array(BI)