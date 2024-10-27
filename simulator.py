#!/usr/bin/env python

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import copy
import curses, curses.ascii, curses.panel
import itertools
import functools
import logging
import math
import optparse
import threading
import time
import traceback

from hydronic import (
    ft, resize, space, portal, environment, C_to_F, F_to_C,
    dimension, area,
    merge, BTU_ft3_F, daytime, fanger
)
from ownercredit import pid, misc
from cpppo.dotdict import dotdict
from cpppo import log_cfg

log_cfg['level']		= logging.INFO
logging.basicConfig( **log_cfg )

structure			= dotdict()

sensor				= {}

# Temperature setpoints (and initial space temperatures), PID loop controllers, etc.
temp				= {}
cntrl				= {}
spaces				= {}
size				= {}

meas				= {}
meas['truss']			= ft(8)			# height of bottom of trusses
meas['rise']			= ft(5)			# rise of roof toward peak
meas['width']			= ft(23)
meas['length']			= ft(49)
meas['side']			= ft(7)			# width of classroom side zones
meas['center']			= meas['width'] - meas['side']*2			# width of classroom side zones
meas['subfloor']		= ft(0,.75)		# 3/4" sheeting
meas['polyaspartic']		= ft(0,.0125)		# 1/8" rolled flooring

# R Values of various substances used in the house
#
# See: http://www.coloradoenergy.org/procorner/stuff/r-values.htm
#
R				= {}
R['SIP3']			= 7.5*3		# Walls
R['SIP4']			= 7.5*4		# Floor, roof
R['window']			= 3		# dual pane w/ internal blinds
R['door']			= 3
R['subfloor']			= 2		# 3/4" ply
R['insulworks']			= 12
R['slab']			= 1		# concrete R1/inch; tubes 1/2 down slab
R['tile']			= .25		# Glue under tile has air spaces...
R['bare']			= .1		# Bare concrete floor
R['fluid']			= .01		# fluid to concrete or subfloor via heat-spreader
R['furniture']			= 10		# Thick, insulative furniture
R['polyaspartic']		= .1		# Close to bare subfloor

# Assumes truss roof rises toward right.  We'll break the classroom in to 3
# segments for radiant control purposes.
# 
size				= {}
size['left']			= ( meas['side'],	meas['length'],	meas['truss'] + meas['rise']*(meas['side']/2)/meas['width'])
size['center']			= ( meas['center'],	meas['length'], meas['truss'] + meas['rise']*(meas['side']+meas['center']/2)/meas['width'] )
size['right']			= ( meas['side'],	meas['length'], meas['truss'] + meas['rise']*(meas['side']+meas['center']+meas['side']/2)/meas['width'] )

roof				= {}

# All interior/exterior insulated connectors.  Each one nets out any windows and doors to its
# connected space...
def wall_avg( parts ):
    htot_weighted		= sum( h*w for w,h in parts )
    wsum			= sum( w   for w,h in parts )
    havg			= htot_weighted / wsum
    return havg,wsum

wall				= { }
wall[('left','world', 'Left')]		= ('SIP3', (meas['length'],meas['truss']))
wall[('left','world', 'Front')]		= ('SIP3', (meas['side'],size['left'][2]))
wall[('left','world', 'Back')]		= ('SIP3', (meas['side'],size['left'][2]))

wall[('center','world', 'Front')]	= ('SIP3', (meas['center'],size['center'][2]))
wall[('center','world', 'Back')]	= ('SIP3', (meas['center'],size['center'][2]))

wall[('right','world','Right')]		= ('SIP3', (meas['length'],meas['truss']+meas['rise']))
wall[('right','world','Front')]		= ('SIP3', (meas['side'],size['right'][2]))
wall[('right','world','Back')]		= ('SIP3', (meas['side'],size['right'][2]))    


# All windows/doors are assumed to be to 'world'
window				= { }
window[('right',   'Gable 1')]	= ( ft(4,0), ft(3,0) )
window[('right',   'Gable 2')]	= ( ft(4,0), ft(3,0) )
window[('right',   'Gable 3')]	= ( ft(4,0), ft(3,0) )
window[('right',   'Gable 4')]	= ( ft(4,0), ft(3,0) )
window[('right',   'Gable 5')]	= ( ft(4,0), ft(3,0) )
window[('center',  'Front')]	= ( ft(4,0), ft(3,0) )

door				= { }
door[('left',  'Entry')]	= ( ft(3),    ft(7) )

# Various floor coverings.  Influences convective heat transfer into space.  Shouldn't affect
# radiance in the long term, as the furniture will (eventually) absorb energy to form a radiant
# extension of the floor it covers.  Therefore, we'll use these to compute the film R value of the
# floor.
def covr_avg( parts ):
    return sum( pct * R[stf] for pct,stf in parts )

covr				= {}
covr['left']			= covr_avg( [(.1, 'furniture'), (.9, 'bare')])
covr['center']			= covr_avg( [(1., 'bare')])
covr['right']			= covr_avg( [(.1, 'furniture'), (.9, 'bare')])

# All zones heat certain areas; slab is assumed, except if a 'joist' entry in roof is found.  The
# first entry in the list is the one assumed to have the air-temperature sensor.  The zone pumps are
# located by matching the corresponding "Zone # Pump" in the sensors file (not the zone alias).
zone				= { }
zone['zone 1']			= [ 'left' ]
zone['zone 2']			= [ 'center' ]
zone['zone 3']			= [ 'right' ]


now				= misc.timer()

world				= space( 'world',  ( 10000.,  10000.,   10000. ),
                                         environment( -40. ), now = now )
ground				= space( 'ground', ( 10000.,  10000.,   10000. ),
                                         environment( C_to_F( 5. ), what = 'soil' ), now = now )
world.contains( ground )

spaces['world']			= world
spaces['ground']		= ground

temp				= {}
temp['']			= C_to_F( 20.0 )

for nm,sz in size.items():
    try:    tmp			= temp[nm]
    except: tmp			= temp['']
    spaces[nm]			= space( nm, sz, environment( tmp ), now = now )
    world.contains( spaces[nm] )

for fo,ts in wall.items():
    frm,out,nam                 = fo    # ( 'garage', 'world', "North" )
    typ,siz			= ts    # ( '8"', ( 59., 9.5 ))
    logging.info( "Wall   %10s <-> %-10s: %-6s, %-12s (%.2fft^2)" % ( frm, out, typ, dimension( siz ), area( siz )))
    # Track down any windows/doors to the same space, and net them out of siz...
    for nn,s in itertools.chain( door.items(), window.items() ):
        if nn[0] == frm and out == 'world':
            siz			= ( siz[0] - s[0]*s[1]/siz[1], siz[1] )
            logging.info( "  - %12s (%.2fft^2) ==> %-12ss (%.2fft^2)" % ( nn[1], area( s ), dimension( siz ), area( siz )))
    spaces[frm].connects( portal( "% 9s/%-9s Wall %s, %s" % ( frm, out, nam, typ ), out, siz, R[typ] ))

# fill any any missing entries in roof.  If you specify any (portion) of a spaces's roof, you must
# specify it all (we'll only fill in an attic roof for missing sized spaces).  We don't include any
# 'joist' roofs here, because they are actually heated floor zones, too...

for du,ts in itertools.chain(
        roof.items(),
        [((k,'world'),('SIP4',size[k])) for k in size.keys()
         if k not in [ d for d,u in roof.keys() ]]
):
    dn,up			= du					# ( 'upstairs', 'world' )
    typ,siz			= ts                                    # ( 'attic', ( 9.333, 39.333 ))
    siz				= ( siz[0], siz[1] )			# tidy up any with extra z dimensions
    if typ != 'joist':
        logging.info( "Roof   %10s <-> %-10s: %-6s, %-12s (% 5.2fft^2)" % (
            dn, up, typ, dimension( siz ), area( siz )))
        spaces[dn].connects( portal( "% 9s/%-9s Roof, %s" % ( dn, up, typ ), up, siz, R[typ] ))

for fn,siz in door.items():
    frm,nam                     = fn    # ( 'garage', 'Car Right' )
    logging.info( "Door   %10s <-> %-10s: R% 5d %-12s (% 5.2fft^2) %s" % (
        frm, 'world', R['door'], dimension( siz ), area( siz ), nam ))
    spaces[frm].connects( portal( "% 9s/%-9s Door %s" % ( frm, 'world', nam ), 'world', siz, R['door'] ))

for fn,siz in window.items():
    frm,nam                     = fn    # ( 'garage', 'North 1' )
    logging.info( "Window %10s <-> %-10s: R% 5d %-12s (% 5.2fft^2) %s" % (
        frm, 'world', R['window'], dimension( siz ), area( siz ), nam ))
    spaces[frm].connects( portal( "% 9s/%-9s Window %s" % ( frm, 'world', nam ), 'world', siz, R['window'] ))


# Is each zone in auto mode, and if so what priority group is it
auto				= {}

# Fanger's equation clo/met variables, for each zone (only if changed from default)
fang				= {}
fang['']			= {}
fang['']['clo']			= 1.0 # casual/indoor
fang['']['met']			= 1.2 # sitting/standing

# Intervals for scaling and clamping.  We can use interval_degrees_C for tuning PID loops, to map a
# certain range of normalized (0,1) error back to a number of degrees C.
interval			= {}
interval['normal']		= (   0.,    1. )		# normalized
interval['celcius']		= ( -30.,   30. )
interval['fahrenheit']		= tuple( C_to_F( c )		# ( -22.,   86. )
                                         for c in interval['celcius'] )
interval['BTU']			= (   0., 25000. )		# BTU/h
interval['percent']		= (   0.,   100. )
interval_degrees_C		= interval['celcius'][1] - interval['celcius'][0]

# 
# P: +/- 2C  error will drive the PID to limit on output.
# 
# I: sum total of 1/60 degree - seconds of error.  1 degree (1/60) of error over 1 hour
# (3600) would add 60 to I.  To make 1 degree - hour of error push the controller output to limit of
# (0,1) requires a Ki of 1/60 (0.01666) (I=60 . 1/60 == 1).  A Ki of 0.001 indicates 16 degree-hours
# of error to push the controller output to limit; 1 degree for 16 hours, or 2 degrees for 8 hours.
# 
# D: The difference between the current error and the last error.
# 
# Lout: range out output values.  Values > 100% (1.0) can be given, to increase
# influence of the zone on secondary heat source (eg. Furnace.)
# 
temp_pid			= {	# PID loop tuning
    '':	{
        'Kpid': [ 
            interval_degrees_C / 2,	# Kp: +/-2 degrees will drive PID to limit
            0.001,			# Ki: .001 --> 16 degree-hours error will drive to limits
            10000.0			# Kd: 10000 --> 1.6 degrees/hour will drive to limits?
        ],
        'Lout':	[
            0.0,			#   0%: Lower output limit
            1.0,			# 100%: Upper output limit.  May be in/decreased
        ],
    }
}


# Each zone is modelled as a volume of water connected to a flooring system.  Each component of the
# flooring system is a space with certain volume and composition, connected to each-other with
# certain insulation qualities.  Each flooring assembly is modelled as follows:
#
#            space     space
#            ------    ------         <-- R0,film=flooring ()
#            space #   space #        <-- wood, tile, etc.
#            ------    ------         <-- flooring insulation
#            slab #    slab # (wood)
#            ------    ------         <-- slab/subfloor insulation
#            zone #    zone #
#  foam -->  ------    ------         <-- ground foam or joist insulation
#            ground    (space below)
#
BTU_ft3_F['polyaspartic']	=  BTU_ft3_F['wood'] #?
for zn,l in zone.items():
    # Get the merged size of the zone in 'zs', from all spaces that share it, and create a floor for
    # each "space" above "zone #", named "space #" Each space holds its own floor, because we want
    # it to be shown in the details window when the space is selected.
    covering			= 'polyaspartic'
    zs				= None
    floor			= []
    for s in l:
        zs			= merge( zs, spaces[s].size )

        # Create a floor for each space, and connect it.  Name it 'space #' (matching 'zone #').
        # This transfers heat in 2 ways into the space; radiant and convective.  We want the radiant
        # temperature of the zone/slab/floor to represent the R value of the physical floor
        # components.  If we set a non-zero R value for this portal, its "inside" temperature for
        # radiant calculations will reflect the interior temperature of the space (net the film R
        # value).  However, a thermal mass should radiate from its surface at its "internal"
        # temperature.  So, we'll always uses R=0, and use the film R value to reflect the flooring
        # thermal resistance.
        fs			= resize( spaces[s].size, h = meas[covering] )
        fn			= zn.replace( 'zone', s )
        spaces[fn]		= space( fn, fs,
                                         environment( spaces[l[0]].conditions.temperature,
                                                      what = covering ),
                                         now = now )
        spaces[s].contains( spaces[fn] )
        spaces[s].connects( portal( "% 9s/%-9s Floor of %s" % ( zn, fn, s ), fn, fs, 
                                    R=0, film=R[covering] ))


    # Estimated piping length on 12" centers, is simply the area of zone.  1/2" sdr-9 PEX contains
    # .92 gallons per 100. ft.  There are 231 cubic inches per gallon.  Spread over the total area
    # of the zone, this gives us the "thickness" of the zone, in inches to yield the volume of water.
    feet			= area( zs )
    gallons			= feet * .92 / 100.
    inches			= gallons * 231 / ( feet * 144 )

    # Create the zone, out of water, add it to world.  Take on the temperature of the
    # first space.  We can't contain it inside a space, because it may span several.
    zs				= resize( zs, h = ft(0,inches))
    spaces[zn]			= space( zn, zs,
                                         environment( spaces[l[0]].conditions.temperature,
                                                      what = 'water' ),
                                         now = now )
    world.contains( spaces[zn] )

    # Find any roof specifying that this space is the upper of the pair, and create portals -- both
    # the upper slab and lower space attach to the zone.
    mass			= 'slab'
    what			= 'wood'
    thick			= meas['subfloor']

    # Connect the zone to the flooring system, via a slab.  We'll assume it is 'concrete', but it
    # may be 'wood' if we've discovered that this is a "joist" zone, just above...  Note that a zone
    # must be *all* concrete slab or joist.  The R value will be that of concrete or floor sheeting.
    ss				= resize( zs, h = thick )
    sn				= zn.replace( 'zone', 'slab' )
    spaces[sn]			= space( sn, ss,
                                     environment( spaces[l[0]].conditions.temperature,
                                                  what = what ),
                                     now = now )
    world.contains( spaces[sn] )
    spaces[sn].connects( portal( "% 9s/%-9s Fluid" % ( zn, sn ), zn, ss,
                                 R['subfloor'], film=0 ))

    # Connect each space's floor to the (subfloor or concrete) slab.  For each 'space' connected to
    # 'zone #', its floor is called 'space #'.  It is directly connected (film=0).
    for s in l:
        fn			= zn.replace( 'zone', s )
        spaces[sn].connects( portal( "% 9s/%-9s Flooring" % ( sn, fn ), fn, spaces[s].size,
                                     R['fluid'], film=0 ))

    if mass == 'slab':
        # The ground sees the radiant heat of a concrete slab zone via SIP panels
        spaces[sn].connects( portal( "% 9s/%-9s Insulation" % ( sn, 'ground' ),
                                     'ground',  ss, R['SIP4'], film=0 ))

    # Create PID Controllers.  Adjusts the number of degree-minutes per hour required to keep the
    # area at a specific temperature.  Go thru each zone, and find the first zone's space that has a
    # temperature setpoint.  TODO: working in BTU/hr for now; convert later...

    # 'zone 1': ( 'garage', pid.controller ).  Use the temperature of the first space on the zone to
    # control the entire zone.
    for z in zone.keys():
        cntrl[z]		= (zone[z][0],None)

    # Now fill in all the pid.controllers.  The setpoint is the space's target temperature, and the
    # process value is its current temperature.  Get the saved Kpid parameters and current I value.
    for z in cntrl.keys():
        s			= cntrl[z][0]
        try:    t		= temp[s]
        except: t		= temp['']

        # Get a deep copy of everything (eg. the Lout list, ...), so that each controller gets its
        # own copy, and also if something changes, we can compare it with the unmodified master when
        # deciding to store it.
        t_pid			= copy.deepcopy( temp_pid.get( '' ))
        t_pid.update( copy.deepcopy( temp_pid.get( s, {} )))

        cntrl[z]		= (s, pid.controller( t_pid['Kpid'],
                                                      setpoint	= misc.scale( t,
                                                                              interval['fahrenheit'],
                                                                              interval['normal'] ),
                                                      process	= misc.scale( spaces[s].conditions.temperature,
                                                                              interval['fahrenheit'],
                                                                              interval['normal'] ),
                                                      output	= 0.,
                                                      Lout	= t_pid['Lout'],
                                                      now	= now ))
        if 'I' in t_pid:
            cntrl[z][1].I	= t_pid['I']


#
# Curses-based Textual UI.
#

def message( window, text, row = 23, col = 0, clear = True ):
    rows,cols			= window.getmaxyx()
    if col < 0 or row < 0 or row >= rows or col >= cols:
        return
    try:
        window.addstr( int( row ), int( col ), text[:cols-col] )
        if clear:
            window.clrtoeol()
    except:
        pass
        #window.addstr( 1, cols - 60, "Couldn't print: (%3d,%3d) %s" % ( col, row, text ))
        #window.refresh()
        #time.sleep( .2 )

#
# pan{siz,loc} -- compute appropriate size and location for sensor detail panel
#
def pansiz( rows, cols ):
    return rows * 9 // 10, cols // 3

def panloc( c, rows, cols ):
    return rows//15, ( c < cols//2 ) and ( cols//2 + cols//10 ) or ( 0 + cols//10 )

def ui( win, cnf ):

    global now
    start			= now
    last			= now
    selected			= 0

    rows, cols			= 0, 0

    # Include every space defined (by size), plus the world (air) and ground
    # Sort include by zone.
    include			= [ 'world', 'ground' ]
    include                    += size.keys()

    def by_zone( sl, sr ):
        zl		= None
        zr		= None
        for z,l in zone.items():
            for s in l:
                if s == sl:
                    zl	= z
                if s == sr:
                    zr	= z
            if zl and zl == zr:
                return l.index(sl) - l.index(sr)			# Both in same zone!  Sort by position
        if zl is None:
            if zr is None:
                return sl < sr and -1 or ( sl > sr and 1 or 0 )		# Neither in zone; sort by name
            else:
                return 1						# Otherwise, None is always after anything
        elif zr is None:
            return -1							# Anything is always before None
        return zl < zr and -1 or ( zl > zr and 1 or 0 )			# Generally, sort by zone

    logging.info("threads: %2d: %s" % (
            threading.active_count(),
            ', '.join( [ t.name for t in threading.enumerate() ] )))

    include.sort( key = functools.cmp_to_key( by_zone ))
    input		= 0
    delta		= 0.0
    while not cnf['stop']:
        message( win, "%s (%7.3f): (%3d == '%c') Quit [qy/n]?, Temperature:% 6.1fC (% 6.1fF) [T/t]"
                 % (  daytime( world.now - world.start ), delta,
                      input, curses.ascii.isprint( input ) and chr( input ) or '?',
                      F_to_C( world.conditions.temperature ), world.conditions.temperature ),
                 row = 0, clear = False )

        # See if we can deduce which (if any) zone PID controller is selected.  
        controllable	= ''
        for z,l in zone.items():
            if include[selected] in l:
                # selected space is in zone's list!  Use default (first) space name
                controllable = z
        if controllable:
            message( win, "%-10s (%-10s) PID: K: [P/p]% 12.6f [I/i]% 12.6f [D/d]% 12.6f, Limit: [L/l]:% 12.6f" % (
                controllable, cntrl[controllable][0],
                cntrl[controllable][1].Kp,
                cntrl[controllable][1].Ki,
                cntrl[controllable][1].Kd,
                cntrl[controllable][1].Lout[1] * 100 ),
                     row = 1, clear = False  )

        curses.panel.update_panels()
        curses.doupdate()

        # End of display loop; display updated; Beginning of next loop; await input
        input			= win.getch()

        # Compute time advance since last thermodynamic update
        real			= misc.timer()
        delta			= real - last

        # Detect window size changes, and adjust detail panel accordingly (creating if necessary)
        if (rows, cols) != win.getmaxyx():
            rows, cols		= win.getmaxyx()
            winsel		= curses.newwin( * pansiz( rows, cols ) + panloc( 0, rows, cols ))
            try:
                pansel.replace( winsel )
            except:
                pansel		= curses.panel.new_panel( winsel )


        # Process input, adjusting parameters
        if 0 < input <= 255 and chr( input ) == 'q':
            cnf['stop'] = True
            return

        if 0 < input <= 255 and chr( input ) == '\f': # FF, ^L
            # ^L -- clear screen
            winsel.clear()

        # Adjust Kp
        if 0 < input <= 255 and chr( input ) == 'P' and controllable:
            inc			= magnitude( cntrl[controllable][1].Kp )
            cntrl[controllable][1].Kp += inc + inc / 100
            cntrl[controllable][1].Kp -= cntrl[controllable][1].Kp % inc
        if 0 < input <= 255 and chr( input ) == 'p' and controllable:
            inc			= magnitude( cntrl[controllable][1].Kp )
            cntrl[controllable][1].Kp -= inc - inc / 100
            cntrl[controllable][1].Kp -= cntrl[controllable][1].Kp % inc

        # Adjust Ki
        if 0 < input <= 255 and chr( input ) == 'I' and controllable:
            inc			= magnitude( cntrl[controllable][1].Ki )
            cntrl[controllable][1].Ki += inc + inc / 100
            cntrl[controllable][1].Ki -= cntrl[controllable][1].Ki % inc
        if 0 < input <= 255 and chr( input ) == 'i' and controllable:
            inc			= magnitude( cntrl[controllable][1].Ki )
            cntrl[controllable][1].Ki -= inc - inc / 100
            cntrl[controllable][1].Ki -= cntrl[controllable][1].Ki % inc

        # Adjust Kd
        if 0 < input <= 255 and chr( input ) == 'D' and controllable:
            inc			= magnitude( cntrl[controllable][1].Kd )
            cntrl[controllable][1].Kd += inc + inc / 100
            cntrl[controllable][1].Kd -= cntrl[controllable][1].Kd % inc
        if 0 < input <= 255 and chr( input ) == 'd' and controllable:
            inc			= magnitude( cntrl[controllable][1].Kd )
            cntrl[controllable][1].Kd -= inc - inc / 100
            cntrl[controllable][1].Kd -= cntrl[controllable][1].Kd % inc

        # Adjust Lout[1] (high output limit); displayed in % (x100)
        if 0 < input <= 255 and chr( input ) == 'L' and controllable:
            inc			= magnitude( cntrl[controllable][1].Lout[1] ) / 10
            cntrl[controllable][1].Lout[1] += inc + inc / 100
            cntrl[controllable][1].Lout[1] -= cntrl[controllable][1].Lout[1] % inc
        if 0 < input <= 255 and chr( input ) == 'l' and controllable:
            inc			= magnitude( cntrl[controllable][1].Lout[1] ) / 10
            cntrl[controllable][1].Lout[1] -= inc - inc / 100
            cntrl[controllable][1].Lout[1] -= cntrl[controllable][1].Lout[1] % inc

        # Shortcut to change world temp (see just below)
        if 0 < input <= 255 and chr( input ) == 'T':
            world.conditions.temperature += 1.801/2
            world.conditions.temperature -= ( world.conditions.temperature - 32. ) % (1.8/2)
        if 0 < input <= 255 and chr( input ) == 't':
            world.conditions.temperature -= 1.799/2
            world.conditions.temperature -= ( world.conditions.temperature - 32. ) % (1.8/2)

        # Select next space, adjust target temp
        if input == curses.ascii.SP:				# ' '
            if pansel.hidden():
                pansel.show()
            else:
                pansel.hide()
        if input in ( curses.ascii.STX, curses.KEY_LEFT, 260 ):	# ^b, <--
            selected		= ( selected - 1 ) % len( include )
        if input in ( curses.ascii.ACK, curses.KEY_RIGHT, 261 ):# ^f, -->
            selected		= ( selected + 1 ) % len( include )

        if input in ( curses.ascii.DLE, curses.KEY_UP, 259 ):	# ^p, ^
            if include[selected] == 'world':                    #     |
                world.conditions.temperature += 1.801/2
                world.conditions.temperature -= ( world.conditions.temperature - 32. ) % (1.8/2)
            elif include[selected] in size:
                try:    temp[include[selected]]  += 1.801/2
                except: temp[include[selected]]   = temp[''] + 1.801/2
                temp[include[selected]]  -= ( temp[include[selected]] - 32. ) % (1.8/2)
            else:
                curses.beep()
        if input in ( curses.ascii.SO, curses.KEY_DOWN, 258 ):  #     |
            if include[selected] == 'world':			# ^n, v
                world.conditions.temperature -= 1.799/2
                world.conditions.temperature -= ( world.conditions.temperature - 32. ) % (1.8/2)
            elif include[selected] in size:
                try:    temp[include[selected]] -= 1.799/2
                except: temp[include[selected]]  = temp[''] - 1.799/2
                temp[include[selected]] -= ( temp[include[selected]] - 32. ) % (1.8/2)
            else:
                curses.beep()

        if 0 < input <= 255 and chr( input ) in ( 'C', 'c', 'M', 'm' ):
            #  Adjust clothing/metabolism, by creating a fanger, and use fanger.clothing()
            s			= include[selected]
            kwds		= copy.copy( fang[''] )
            if s in fang:
                kwds.update( fang[s] )
            f			= fanger( **kwds )
            amt, clo, dsc	= f.clothing()
            if chr( input ) in ( 'C', 'c' ):
                amt            	= max( 0, min( 1., ( amt + (.05 if chr( input ) == 'C' else -.05 ))))
                amt, clo, dsc	= f.clothing( amount=amt )
                fang.setdefault( s, {} )["clo"] = clo
            rate, met, dsc	= f.metabolism()
            if chr( input ) in ( 'M', 'm' ):
                rate           	= max( 0, min( 1., ( rate + (.05 if chr( input ) == 'M' else -.05 ))))
                rate, met, dsc	= f.metabolism( rate=rate )
                fang.setdefault( s, {} )["met"]	= met


        # When a keypress is detected, always loop back and get another key, to absorb multiple
        # keypresses (eg. due to key repeat), but only do it if less then 1/3 second has passed.
        if 0 < input and delta < .3:
            continue

        # We'll be computing a new thermodynamic model; advance (and remember) time
        last			= real
        now                     = real

        # Compute the heat gain/loss for each zone over the last time period.
        results			= world.compute( now=now )

        # For "simulated" zones (with no temperature sensors in their slab##), add in the heat added
        # to each zone over the last time period.  This uses the *previous* time period's computed
        # degree-minutes per hour computation for the zone.

        # TODO: we'll work the PID loop in simple BTU/hour for now...  So, the computed
        # BTU/hour is scaled by the delta (in seconds) elapsed during the last time period.
        # Fake up a key to represent heat added to the zone## water by the pumps.

        adjusted		= copy.copy( results )
        for z in cntrl.keys():
            s			= z.replace( 'zone', 'slab' )
            if s in spaces:
                # Zone with slab sensor.  
                sen		= spaces[s].conditions.sensor
                if sen:
                    with sen.lock:
                        act	= sen.compute( max( now, sen.now ))
                    if not misc.non_value( act ) and 0.0 < act < 40.0:
                        cur	= C_to_F( act )
                        spaces[s].conditions.temperature \
                                = spaces[z].conditions.temperature \
                                = cur
                        continue
         
                    logging.debug( "%s == %s: Invalid sensor; ignoring" % ( s, str( act )))

            # zone has no slab sensor, or a broken slab sensor; use the zone's
            # primary aliases' current temperature.
            alias		= zone[z][0]
            spaces[s].conditions.temperature \
                = spaces[z].conditions.temperature \
                = spaces[alias].conditions.temperature

            '''
            # Used to try to compute how much heat we added, in BTU/hr, but we
            # didn't have the supply/return temps or flow rates; could do a
            # similar computation but would have to run the PID loop on
            # degree-minutes per hour, which we can compute, and would be a
            # consistent measure of energy over time for each zone.
            k			= ( z, 'hydronic', 'pumps' )
            if k not in adjusted:
                adjusted[k]	= 0.
            adjusted[k]        += misc.scale( cntrl[z][1].value,
                                              interval['normal'], interval['BTU'] ) * delta / 60 / 60
            '''


        # And finally, apply the net BTU gains/losses to the world.  This estimates the temperature
        # conditions of every space and surface in the world.
        world.absorb( adjusted )


        # Next frame of animation
        win.erase()

        # Reserve a top margin for screen, and a bottom margin for each rank in the pile
        templo                  = -40
        temphi                  = +120
        temprange		= temphi - templo

        topmargin               = 2
        botmargin               = 9
        botrow			= rows - botmargin

        # Compute screen size and display headers.  We want cells about 3 times as high as wide, and
        # at least 20 characters wide.  Keep piling 'til we are either over 20 characters wide, or
        # less than 3 times as high as wide.
        try:
            areas		= len( include )
            pile		= 1
            rank		= areas // pile
            height		= rows - topmargin
            width		= cols // ( rank + 1 )
            while width < 15 or height >= 5 * width / 4:
                pile           += 1
                rank		= ( areas + pile - 1 ) // pile	# ensure integer div rounds up
                height		= ( rows - topmargin ) // pile
                width		= cols // ( rank + 1 )
            if height < 10:
                raise
        except:
            message( win, "Insufficient screen size (%d areas, %d ranks of %dx%d); increase height/width, or reduce font size" % (
                areas, pile, width, height ),
                     col = 0, row = 0 )
            time.sleep( 2 )
            continue


        # Compute the range of rows used to span the entire temperature range
        rowrange		= height - botmargin

        for p in range( 0, pile ):
            r			= rows - p * height

            message( win, "zone (volume):",         col = 0, row = r - 9 )
            message( win, "zone/slab/floor (C):",   col = 0, row = r - 8 )
            message( win, "Heat Call/Load:",        col = 0, row = r - 7 )
            message( win, "P(%):",                  col = 0, row = r - 6 )
            message( win, "I(%):",                  col = 0, row = r - 5 )
            message( win, "D(%):",                  col = 0, row = r - 4 )
            message( win, "Air/Rad.(C), Comfort:",  col = 0, row = r - 3 )
            message( win, "BTU/h Load:",            col = 0, row = r - 2 )
            message( win, "Space:",                 col = 0, row = r - 1 )

            Rtemprows		= ( r - botmargin + 1, r - height + 1 ) # Inverted domain->range mapping
            for rt in range ( Rtemprows[1], Rtemprows[0] ):
                rtemp		= misc.scale( rt, Rtemprows, interval['fahrenheit'] )
                message( win, "% 7.2F (% 7.2fC)" % ( rtemp, F_to_C( rtemp )),
                         col = 0, row = rt, clear = False )

        def space_pos( a, n ):
            p			= pile - a // rank - 1
            c			= width + width * ( a % rank )
            r			= rows - p * height
            return (p,c,r)

        for a in range( 0, len( include )):
            s			= include[a]

            # Sum up all the BTU gain/loss by the space from/to other spaces via
            # each portal.  Remember them in spaces[<name>].load, so we can
            # return them on demand via the web JSON API.  Compute the btu/h,
            # ft^2 and radiant temperature of each portal, and compute the total
            # average radiant temperature for Fanger's equation.
            btu			= 0.
            btudct		= {}
            area		= 0.
            radiant		= 0.
            inside		= spaces[s].conditions
            for rs,ro,rp in results.keys():
                if rs != s:
                    continue
                val		= results[(rs,ro,rp)]
                btu            += val
                btu_h		= val * 60*60 / delta
                outside		= spaces[ro].conditions
                portal		= None
                for p in spaces[rs].portals:
                    if ro == p.onto and rp == p.name:
                        # This space 'rs' has a portal onto other space 'ro'
                        # Compute portal's inside temperature facing us
                        portal	= p
                        break
                if portal is None:
                    for p in spaces[ro].portals:
                        if rs == p.onto and rp == p.name:
                            # Other space 'ro' has a portal onto this space 'rs'
                            # Compute portal's outside temperature facing us
                            portal = p
                            break;

                if portal is None:
                    logging.info( "Couldn't find portal named %s" % ( rp ))
                pt      	= portal.temperature( inside=inside, outside=outside )
                pa      	= portal.area()
                area           += pa
                radiant        += pa * pt
                btudct[(rs,ro,rp)] = (btu_h, pa, F_to_C( pt ),
                                      F_to_C( inside.temperature ),
                                      F_to_C( outside.temperature ),
                                      portal.R)

            spaces[s].load	= btudct
            spaces[s].radiant	= radiant/area if area > 0 else inside.temperature


            kwds		= copy.copy( fang[''] )
            if s in fang:
                kwds.update( fang[s] )
            kwds["hum"]		= 0.5
            kwds["t_r"]		= F_to_C( spaces[s].radiant )
            kwds["t_a"]		= F_to_C( inside.temperature )
            try:
                spaces[s].fanger= fanger( **kwds )
                pmw             = spaces[s].fanger.L()
                feels		= spaces[s].fanger.feels()
                _, clo, clostr	= spaces[s].fanger.clothing()
                _, met, metstr	= spaces[s].fanger.metabolism()
            except Exception as exc:
                pmw		= 0.0
                feels		= "unknown"
                clo, clostr	= math.nan, "unknown"
                met, metstr	= math.nan, "unknown"
                logging.warning( "Fanger failure: args: %r; %s", kwds,
                                 exc if not logging.getLogger().isEnabledFor( logging.INFO ) else traceback.format_exc() )
                #raise

            p,c,r		= space_pos( a, len( include ))

            # Find the controls for this zone "<z> #".
            for z,l in zone.items():
                if s in l:
                    # This space's temperature is controlled by this zone's
                    # heated floor sandwich:
                    #
                    # <space>    <space>    <space>     Spaces...
                    #
                    # <space> #  <space> #  <space> #   Floors...
                    # -------------------------------
                    #               slab #              Slab
                    # -------------------------------
                    #               zone #              Zone  (fluid)
                    fl 		= z.replace( 'zone', s )
                    sl		= z.replace( 'zone', 'slab' )
                    message( win, "|%c%5.1f%c%5.1f%c%5.1f" % (
                        '*' if spaces[z].conditions.sensor else ' ',
                        F_to_C( spaces[z].conditions.temperature ),
                        '*' if spaces[sl].conditions.sensor else '/',
                        F_to_C( spaces[sl].conditions.temperature ),
                        '*' if spaces[fl].conditions.sensor else '/',
                        F_to_C( spaces[fl].conditions.temperature )),
                             col = c, row = r - 8 )

                if s == l[0]:
                    # Display PID loop data only in first (primary) space above zone
                    spaces[s].heatcall	= misc.scale( cntrl[z][1].value,
                                                      interval['normal'],
                                                      interval['percent'] ) # was 'BTU'
                    try:
                        Pp,Pi,Pd	= cntrl[z][1].contribution()

                        message( win, "| %7.3f%%/%9.3f" % ( spaces[s].heatcall, btu *60*60/delta ),
                                 col = c, row = r - 7 )
                        message( win, "|% 13.8f % 4d%%" % ( cntrl[z][1].P, Pp * 100 ),
                                 col = c, row = r - 6 )
                        message( win, "|% 13.8f % 4d%%" % ( cntrl[z][1].I, Pi * 100 ),
                                 col = c, row = r - 5 )
                        message( win, "|% 13.8f % 4d%%" % ( cntrl[z][1].D, Pd * 100 ),
                                 col = c, row = r - 4 )
                    except:
                        message( win, "%s?" % ( z ), col = c, row = r - 9 )

            message( win, "|" + s + " %3.1f/%s" % ( clo, clostr ) + " %3.1f/%s" % ( met, metstr ),
                     col = c, row = r - 1 )

            # Current and target temperature.  If a space has a sensor, we'll update the current
            # conditions temperature from the sensor (using the value's current time, 'cause it is
            # being updated in the background, and may have a time already after our own 'now' cycle
            # time), and display the error in +/- degrees/hour between the computed temperature
            # (from heat gain/loss from all adjoining spaces), and the measurement.  This will give
            # us some idea of the error in our thermodynamic model...
            t			= temp['']
            if s in temp:
                t		= temp[s]
            if s == include[selected]:
                win.attron(curses.A_REVERSE);

            Rtemprows		= ( r - botmargin + 1, r - height + 1 ) # Inverted domain->range mapping
            if s in size.keys():
                message( win, "% 6.1fC>" % ( F_to_C( t )),
                         col = c, row = misc.clamp( misc.scale( t, interval['fahrenheit'], Rtemprows ),
                                                    ( Rtemprows[1], Rtemprows[0] )))
            cur			= spaces[s].conditions.temperature
            sen			= spaces[s].conditions.sensor
            if sen:
                with sen.lock:
                    act		= sen.compute( max( now, sen.now ))
                if not misc.non_value( act ):
                    cur		= C_to_F( act )
                    spaces[s].conditions.temperature \
                                = cur

            # Current (averaged over several minutes, if sensor available), or computed
            message( win, "|%4.1f/%4.1fC %3.1f/%s" % (
                F_to_C( spaces[s].conditions.temperature ),
                F_to_C( spaces[s].radiant ), pmw, feels ),
                     col = c, row = r - 3 )

            # Current temperature, and automation mode
            automation		= auto.get( s, 0 )
            message( win, "=% 5.1fC %s" % (
                F_to_C( cur ), ( "(manual)" if automation == 0 else
                                 "(temp.)"  if automation == 1 else
                                 "(fanger)" if automation == 2 else
                                 "(unknown)" )),
                     col = c + 8, row = misc.clamp( misc.scale( cur, interval['fahrenheit'], Rtemprows ),
                                                   ( Rtemprows[1], Rtemprows[0] )))
            if s == include[selected]:
                win.attroff(curses.A_REVERSE);

        # Run the PID controllers for this time period, to compute next time period's
        # BTU/hour contributions.  Condition the input and output to be in range (0,1)

        for z in cntrl.keys():
            try:    t           = temp[cntrl[z][0]]
            except: t		= temp['']
            cntrl[z][1].loop(
                setpoint	= misc.scale( t,
                                              interval['fahrenheit'], interval['normal'] ),
                process		= misc.scale( spaces[cntrl[z][0]].conditions.temperature,
                                              interval['fahrenheit'], interval['normal'] ),
                now		= now )

        # Make h- and v-bars, everwhere except top margin
        for r in range( topmargin, rows ):
            if ( rows - r ) % height == 0:
                win.hline( r, 0, curses.ACS_HLINE, cols )
        for c in range( width, cols, width):
            win.vline( topmargin, c, curses.ACS_VLINE, rows - topmargin )

        message( win, "%2d areas on %3dx%3d screen ==> %3d x %2d/rank @ %2dx%2d" % (
                    areas, cols, rows, pile, rank, width, height ),
                 col = cols - 60, row = 0, clear = False )

        # Itemize the gain/loss details for all portals in the selected area.  Sort all of the
        # selected space's gain/loss by area.  Move 'winsel's curses.panel 'pansel' clear of its
        # detail's location...  If window is resized, this may fail; if so, loop and recompute..
        p,c,r			= space_pos( selected, len( include ))
        try:
            pansel.move( * panloc( c, rows, cols ))
        except:
            continue

        winsel.erase()
        wsrows, wscols		= winsel.getmaxyx()


        r			= 2
        try:   winsel.hline( r, 1, curses.ACS_HLINE, wscols - 2 )
        except: pass
        r                      += 1

        # Get selected keys, sorted by absolute gain/loss
        sel			= sorted(
                                    [ k for k in adjusted.keys()
                                        if k[0] == include[selected] ],
                                    key=lambda x: abs(adjusted.__getitem__(x)), reverse=True)

        btu			= 0.
        for s,o,p in sel:
            #message( winsel, "%s %s %s" % ( s, o, p ), col=2, row=r )
            #r                 += 1
            b                   = adjusted[(s,o,p)] * 60 * 60 / delta
            btu                += b
            # Search the entire world, find the portal, by name, and get its area and R value
            for space, depth in world.walk():
                for portal in space.portals:
                    if ( p == portal.name and ( s == space.name or s == portal.onto )):
                        message( winsel, "% 10.3f %s %-10s %4d' R%-2d %-30s" % (
                            b, b < 0 and "-->" or "<--", o, portal.area(), portal.R, p  ),
                                 col = 2, row = r )
                        r                      += 1
                        break

        try:    winsel.hline( r, 1, curses.ACS_HLINE, wscols - 2 )
        except: pass
        r                      += 1

        message( winsel, "%10.3f %s %-12s % 4.1fC" % (
            btu, btu < 0 and "<--" or "-->", include[selected],
            F_to_C( spaces[include[selected]].conditions.temperature )),
                 col = 2, row = 1, clear = False )


        # Now, scan all the sub-spaces (floors), and the associated slabs and zones.
        # "space"  ==> "space #" (floor), "slab #" and  "zone#".
        floor			= None
        for s in spaces[include[selected]].subspaces:
            message( winsel, 15 * ' ' + "%-12s % 4.1fC" % ( s.name, F_to_C( s.conditions.temperature ) ),
                     col = 2, row = r, clear = False )
            r                  += 1
            sn			= s.name.replace( include[selected], 'slab' )
            zn			= s.name.replace( include[selected], 'zone' )
            if sn and sn != s and sn in spaces:
                message( winsel, 15 * ' ' + "%-12s % 4.1fC" % ( sn, F_to_C( spaces[sn].conditions.temperature )),
                         col = 2, row = r, clear = False )
                r              += 1
            if zn and zn != s and zn in spaces:
                message( winsel, 15 * ' ' + "%-12s % 4.1fC" % ( zn, F_to_C( spaces[zn].conditions.temperature )),
                         col = 2, row = r, clear = False )
                r              += 1

        # Output any sensor temperatures that will fit
        try:
            for k in sorted(sensor.keys(), key=misc.natural):
                sen		= sensor[k]
                with sen.lock:
                    t           = sen.compute( now=max( now, sen.now ))
                if t is None:
                    message( winsel, "%-32.32s: (not updated)" % ( k, ),
                             col = 2, row = r, clear = False )
                else:
                    message( winsel, "%-32.32s: %7.3f" % ( k, t ),
                             col = 2, row = r, clear = False )
                r                      += 1
        except Exception as e:
            message( winsel, "Exception: %s" % ( str(e), ),
                     col = 2, row = r, clear = False )
            r                          += 1

        winsel.border( 0 )

    # Final refresh (in case of error message)
    win.refresh()


def txtgui( cnf ):
    # Run curses UI, catching all exceptions.  Returns True on failure.
    failure			= None
    try:        # Initialize curses
        stdscr			= curses.initscr()
        curses.noecho();
        curses.cbreak();
        curses.halfdelay( 1 )
        stdscr.keypad( 1 )

        ui( stdscr, cnf )               # Enter the mainloop
    except KeyboardInterrupt:
        pass
    except:
        failure			= traceback.format_exc()
    finally:
        cnf['stop']		= True
        stdscr.keypad(0)
        curses.echo() ; curses.nocbreak()
        curses.endwin()
        time.sleep(.25)
    if failure:
        logging.error( "Curses GUI Exception: %s", failure )
        return True
    return False


if __name__=='__main__':

    parser = optparse.OptionParser()
    parser.add_option( '-f', '--fake', dest='fake',
                       action="store_true", default=False,
                       help='Simulate sensors (default: False)')
    (options, args) = parser.parse_args()

    txtcnf			= { 'stop': False }
    txtgui( txtcnf )
