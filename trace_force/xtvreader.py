#!/usr/bin/env python3

"""
    xtvreader.py is a native Python module for retrieving values from a
    TRACE XTV file.  It exposes one class that contains several methods
    which make this possible.

    This module is a bit confusing upon review, but this is mostly
    because it's built around a somewhat clunky specification for the file format.
    Basically, when an XTVFILE object is instantiated, it reads the header
    information and spawns additional objects for each component.

    The header information is sufficient for the code to determine exactly which
    bytes must be read to extract a specific data point at a specific edit.  Then
    there are a series of methods to grab either a single data point, or a list of
    points (or point pairs) along an x or z axis, or values over time.  If times
    or axial locations are requested that are between edits or mesh indices, the
    value is interpolated linearly from adjacent edits or mesh points.

    How is this module different from AptPlot and PyPost?  Well, those tools are
    standalone applications that you interact with either using a GUI or by writing
    separate batch scripts.  AptPlot contains its own custom batch language, while
    PyPost uses an underlying Jython interpreter to allow batch scripts to be written
    using close to the full power of Python 2.7.  PyPost does, however, still carry
    with it some klunky design choices of the AptPlot batch command syntax.

    Conversely, this module can be imported into any existing native Python script,
    giving you the ability to directly interact with an XTV file without needing to
    spawn/fork an external process (which tends to be slow) or ensure that the script
    syntax conforms to the Jython variant of Python (rendering it less portable).

    Some of the limitations are that it does not contain many (or any, really) of the
    helper functions that the AptPlot and PyPost batch language contains.

"""

__author__ = """Christopher Murray, (Christopher.Murray@nrc.gov)\nJosh Whitman, (Joshua.Whitman@nrc.gov)"""

from . import xdrfile
from collections import namedtuple, OrderedDict
from itertools import chain
import traceback
import bisect
import re
# These modules needed only for testing
import subprocess
import argparse
import textwrap
import sys
import os
import ast


_fineMeshVars = ['depthZrRxIn', 'depthZrRxOn', 'eCR50_46', 'hgap', 'hrfgi', 'hrfgo', 'hrfli', 'hrflo', 'hrfvi',
                'hrfvo', 'ihtfi', 'ihtfo', 'qchfi', 'qchfo', 'rftn', 'tchfi', 'tchfo', 'tmini', 'tmino',
                'xcriti', 'xcrito', 'zht']

_rodVars = ['eTransi','eTranso','hgap','pgapt','volGas','tempGas','vFGap','vFFRPlen','vFCrack','vFDish',
           'vFRough','vFPore','vFInt','vFCH','eCR50_46','depthZRO','depthZRI','zht','hrfli','hrfvi',
           'tmini','hrfgi','hrflo','hrfvo','tmino','hrfgo','ihtfi','ihtfo','qchfi','qchfo','tchfi',
           'tchfo','rftn','pLossi','eLossAi','eLossEi','pLosso','eLossAo','eLossEo',]


err_codes = {
             'TIME_UBOUND_ERR'     : "!! XTV Error !! - Requested time point beyond last time edit",
             'TIME_LBOUND_ERR'     : "!! XTV Error !! - Requested time point is before first time edit",
             'TIME_EMPTY_ERR'      : "!! XTV Error !! - XTV file is empty - it appears to contain no state data",
             'ERR_XRYT_INDEX'      : "!! XTV Error !! - XR or YT index set > 0 for a variable that does not use them",
             'ERR_YT_INDEX'        : "!! XTV Error !! - YT index set > 0 for a variable that does not use it",
             'ERR_AXRYT_INDEX'     : "!! XTV Error !! - A, XR or YT index set > 0 for a variable that does not use them",
             'INVALID_CHANNEL1'    : "!! XTV Error !! - The requested variable name, component type, and/or component ID are unknown",
             'INVALID_CHANNEL2'    : "!! XTV Error !! - While decoding channel ID, got an unknown channel name or component ID",
             'INDEX_I_LBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - i must be > 0",
             'INDEX_J_LBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - j must be > 0",
             'INDEX_K_LBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - k must be > 0",
             'INDEX_I_UBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - i is too large",
             'INDEX_J_UBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - j is too large",
             'INDEX_K_UBOUND_ERR'  : "!! XTV Error !! - Invalid mesh index value - k is too large",
             'AXIAL_UBOUND_ERR'    : "!! XTV Error !! - Invalid axial height - value extends beyond last mesh point",
             'AXIAL_LBOUND_ERR'    : "!! XTV Error !! - Invalid axial height - value comes before first mesh point",
             'AXIAL_SCALAR_ERR'    : "!! XTV Error !! - Axial distance requested for a scalar data channel",
             'HDR_UNPACK_ERR'      : "!! XTV Error !! - Failed to unpack the XTV Starting Block",
             'HDR_FORMAT_ERR'      : "!! XTV Error !! - Unsupported XTV format - only MUX format files can be parsed",
             'RECORD_LEN_ERR'      : "!! XTV Error !! - Header record length does not match Starting Block data length - channel offsets would be wrong",
             'TIME_ORDER_ERR'      : "!! XTV Error !! - Time points in the XTV file are not in increasing order",
            }


class _Component(object):
    def __init__(self, number, compType):
        self.number = int(number)
        self.compType = compType
        self.channels = OrderedDict()
        self.dimensions = None
        self.nJuns = 0

        self.nTempl = 0
        self.templates = []

        self.nLegs = 0
        self.sidelegs = []

        self.nDynAx = 0
        self.dynAxes = []


class _Channel(object):
    def __init__(self, comp, name, startIncrement):
        self.name = name
        self.comp = comp
        self.ncells = None
        self.startIncrement = startIncrement


class _Template(object):
    def __init__(self, name):
        self.name = name
        #self.index = index
        self.nCells = 0
        self.nCellsI = 0
        self.nCellsJ = 0
        self.nCellsK = 0
        self.dimi = 0
        self.dimj = 0
        self.dimk = 0
        self.coordSys = ''
        self.coordi = ''
        self.coordj = ''
        self.coordk = ''
        self.dynAxI = 0
        self.dynAxJ = 0
        self.dynAxK = 0
        self.fI = []
        self.fJ = []
        self.fK = []
        self.grav = []
        self.fa = []


class _DynamicAxis(object):
    def __init__(self):
        self.dsAx = ''
        self.varType = ''
        self.sVarName = ''
        self.lVarName = ''
        self.vMax = 0


class _SideLeg(object):
    def __init__(self, sCell, eCell, jCell):
        self.sCell = sCell
        self.eCell = eCell
        self.jCell = jCell


class XTVError(Exception):
    """
    Custom XTV-specific exception handler (subclassed from the generic
    built-in Exception handler class.

    This exception is raised when a data retrieval request for the XTV file
    cannot be fulfilled because the data channel (or any of its constituent
    fields) is not correct or does not otherwise exist, or the time or the
    z location is out of bounds for the component and variable name of
    interest.

    """

    # Use this for any XTV-specific errors like invalid data channel names,
    # invalid time requests, etc

    def __init__(self, errmsg, filename=None):
        self.err = (errmsg, filename)
        Exception.__init__(self, errmsg, filename)

    def __iter__(self):
        return iter(self.err)

class XtvFile(object):
    """
    Creates a new XtvFile object.

    Takes a filehandle to an open XTV file, reads & processes its header
    information, and returns an XtvFile object ready for further operations.
    The file handle is left open for further reading.

    Args:
        xtvFile (file): an open file handle to the XTV file

        verbose (logical): optional argument requesting a higher level of verbosity while
        processing the header information

    Returns:
        An XtvFile object

    Example::

       import xtvreader

       # Open the XTV fiie and save the filehandle
       xtv_file = "path/to/file.xtv"
       with open(xtv_file, 'rb') as xtvFileHandle:

           # Instantiate an XtvFile object with the open filehandle.  This will read
           # in and parse the XTV header information
           xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

           .....    # do some processing on the object

    """

    #  For the status, the following values apply:
    #    0 : calculation started but no data has yet been written to the XTV file
    #    1 : the calculation is in progress.  XTV file has some data but not complete
    #    2 : the calculation is complete.  The XTV file is complete
    __StartingBlock = namedtuple('StartingBlock', 'hdrString xtvMajorV xtvMinorV revNumber '
                             + 'xtvRes nUnits nComp nSVar nDVar nSChannels nDCannels dataStart '
                             + 'dataLen nPoints status spare1 spare2 spare3 fmtString unitsSys '
                             + 'sysName osString sDate sTime title')


    def __init__(self, xtvFile, verbose=False): # file handle for opened file
        """Initializes a new XtvFile instance"""

        self.xtvFile = xtvFile
        self.components = OrderedDict()
        self.times = []
        self.verbose = verbose
        self.up = xdrfile.FileUnpacker(self.xtvFile)

        try:
            self.SB = self.__StartingBlock(self.up.unpack_string(), *tuple(chain.from_iterable((tuple(
                self.up.unpack_int() for i in range(17)), tuple(self.up.unpack_string() for i in
                                                                           range(7))))))
        except Exception:
            print("Something went wrong unpacking the Starting Block")
            print( traceback.format_exc())
            raise XTVError(err_codes['HDR_UNPACK_ERR'], self.xtvFile.name)

        if self.SB.fmtString != "MUX":
            print("Can only parse XTV files. This file is in the format " + self.SB.fmtString)
            raise XTVError(err_codes['HDR_FORMAT_ERR'], self.xtvFile.name)

        if self.verbose:
            print(self.SB)

        stride = 20
        recordLength = stride + self.SB.xtvRes
        try:
            while True:
                start = self.up.get_position()
                if self.verbose:
                    print
                    print("Block starting location: " + str(start))

                # The first three values of a block are the block type,
                # revision number, and size of the block.  The data for the block
                # follows and will depend upon what type of block it is
                blockType = self.up.unpack_string()
                if self.verbose:
                    print("Block Type = " + blockType)

                revision = self.up.unpack_int()  # don't need this

                jump = self.up.unpack_int()
                if self.verbose:
                    print("Blocksize = " + str(jump))


                if blockType == "GCHd":
                    compID = self.up.unpack_int()
                    if self.verbose:
                        print("   compID = " + str(compID))

                    self.up.unpack_int()

                    compType = self.up.unpack_string()
                    if self.verbose:
                        print("   compType: " + compType)

                    currentComp = _Component(compID, compType)
                    self.components[(compID,compType.strip())] = currentComp

                    currentComp.title = self.up.unpack_string()
                    currentComp.dim = self.up.unpack_int()

                    currentComp.nTempl = self.up.unpack_int()
                    currentComp.templates.append(None)  # Would be convenient to make this list start at 1.  As a kluge, make the 0 element None

                    currentComp.nJuns = self.up.unpack_int()
                    currentComp.nLegs = self.up.unpack_int()

                    nSVar = self.up.unpack_int()
                    nDVar = self.up.unpack_int()
                    nVect = self.up.unpack_int()
                    nChild = self.up.unpack_int()
                    currentComp.nDynAx = self.up.unpack_int()

                elif blockType == "GD3D":
                    currentTempl = _Template("GD3D")
                    currentTempl.nCells = self.up.unpack_int()
                    currentTempl.nCellsI = self.up.unpack_int()
                    currentTempl.nCellsJ = self.up.unpack_int()
                    currentTempl.nCellsK = self.up.unpack_int()
                    currentTempl.dimi = currentTempl.nCellsI
                    currentTempl.dimj = currentTempl.nCellsJ
                    currentTempl.dimk = currentTempl.nCellsK
                    currentTempl.dynAxI = self.up.unpack_int()
                    currentTempl.dynAxJ = self.up.unpack_int()
                    currentTempl.dynAxK = self.up.unpack_int()
                    currentTempl.coordSys = self.up.unpack_string().strip()
                    if self.verbose:
                        print("  Coordsys = " + currentTempl.coordSys)
                    if currentTempl.coordSys == "CART3D":
                        currentTempl.coordi = 'x'
                        currentTempl.coordj = 'y'
                        currentTempl.coordk = 'z'
                    elif currentTempl.coordSys == "CYL3D":
                        currentTempl.coordi = 'r'
                        currentTempl.coordj = 't'
                        currentTempl.coordk = 'z'

                elif blockType == "GD3A":    # Assumption is that this block will always appear right after 'GD3D'

                    currentTempl.fI = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.fJ = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.fK = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.grav = self.up.unpack_array(self.up.unpack_double)

                    currentComp.templates.append(currentTempl)

                elif blockType == "GD2D":
                    currentTempl = _Template("GD2D")
                    currentTempl.nCells = self.up.unpack_int()
                    currentTempl.nCellsI = self.up.unpack_int()
                    currentTempl.nCellsJ = self.up.unpack_int()
                    currentTempl.dimi = currentTempl.nCellsI
                    currentTempl.dimj = currentTempl.nCellsJ
                    currentTempl.dynAxI = self.up.unpack_int()
                    currentTempl.dynAxJ = self.up.unpack_int()
                    currentTempl.coordSys = self.up.unpack_string().strip()
                    if self.verbose:
                        print("  Coordsys = " + currentTempl.coordSys)
                    if currentTempl.coordSys == "CART2D":
                        currentTempl.coordi = 'x'
                        currentTempl.coordj = 'y'
                    elif currentTempl.coordSys == "CYLRZ":
                        currentTempl.coordi = 'r'
                        currentTempl.coordj = 'z'
                    elif currentTempl.coordSys == "CYLRT":
                        currentTempl.coordi = 'r'
                        currentTempl.coordj = 't'
                    elif currentTempl.coordSys == "CYLTZ":
                        currentTempl.coordi = 't'
                        currentTempl.coordj = 'z'

                elif blockType == "GD2A":  # Assumption is that this block will always appear right after 'GD2D'

                    currentTempl.fI = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.fJ = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.grav = self.up.unpack_array(self.up.unpack_double)

                    currentComp.templates.append(currentTempl)

                    pass

                elif blockType == "GD1D":
                    currentTempl = _Template("GD1D")
                    currentTempl.nCells = self.up.unpack_int()
                    currentTempl.nCellsI = currentTempl.nCells
                    currentTempl.dimi = currentTempl.nCells
                    currentTempl.dynAxI = self.up.unpack_int()
                    currentTempl.coordi = 'x'

                elif blockType == "GD1A":  # Assumption is that this block will always appear right after 'GD1D'

                    currentTempl.fI = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.grav = self.up.unpack_array(self.up.unpack_double)
                    currentTempl.fa = self.up.unpack_array(self.up.unpack_double)

                    currentComp.templates.append(currentTempl)
                    pass

                elif blockType == "GDLg":
                    sCell = self.up.unpack_int()
                    eCell = self.up.unpack_int()
                    jCell = self.up.unpack_int()
                    currentLeg = _SideLeg(sCell, eCell, jCell)

                    if self.verbose:
                        print("  Sideleg found for component = " + str(compID))
                        print("  Sideleg starts at cell = " + str(sCell) + " and ends at cell = " + str(eCell))
                        print("  Sideleg connected to jCell = " + str(jCell))

                    currentComp.sidelegs.append(currentLeg)

                elif blockType == "DsAx":
                    dynAxis = _DynamicAxis()
                    dynAxis.dsAx = self.up.unpack_string()
                    dynAxis.varType = self.up.unpack_string()
                    dynAxis.sVarName = self.up.unpack_string()
                    dynAxis.lVarName = self.up.unpack_string()
                    dynAxis.vMax = self.up.unpack_int()

                    currentComp.dynAxes.append(dynAxis)

                elif blockType == "VARD":
                    varName = self.up.unpack_string()
                    if self.verbose:
                        print("  Variable name = " + varName)
                        print("  compType:compID = " + compType + ":" + str(compID))

                    currentChannel = _Channel(currentComp, varName, recordLength)
                    currentComp.channels[varName.strip()] = currentChannel
                    currentChannel.varLabel = self.up.unpack_string()
                    currentChannel.uType = self.up.unpack_string()
                    currentChannel.uLabel = self.up.unpack_string()
                    currentChannel.dimPosAt = self.up.unpack_string()
                    currentChannel.freqAt = self.up.unpack_string()
                    currentChannel.cMapAt = self.up.unpack_string()
                    currentChannel.vectAt = self.up.unpack_string()
                    currentChannel.spOptAt = self.up.unpack_string()
                    currentChannel.vectName = self.up.unpack_string()
                    currentChannel.vTmpl = self.up.unpack_int()
                    currentChannel.vLength = self.up.unpack_int()
                    if currentChannel.freqAt == "TD":
                        recordLength += currentChannel.vLength * self.SB.xtvRes

                elif blockType == "DATA":
                    break

                self.up.set_position(start+jump)
        except EOFError:
            # End of file reached before a DATA block.  Legitimate for an XTV file
            # whose calculation has not yet written any data records; genuine header
            # damage is caught by the record-length check below.  Any other exception
            # now propagates - a misparsed header must never be silently read from.
            pass

        #print("XTV Precision = " + str(self.SB.xtvRes))
        #print("Data Length = " + str(self.SB.dataLen))
        #print("Record Length = " + str(recordLength))
        # Reconciliation cross-check: the record length accumulated from the TD
        # channels in the header must equal the per-edit data length declared in the
        # Starting Block.  A mismatch means every later channel offset is wrong and
        # reads would silently return a neighboring variable's data.  Raised rather
        # than asserted - asserts are stripped under python -O.
        if recordLength != self.SB.dataLen:
            raise XTVError(err_codes['RECORD_LEN_ERR'], self.xtvFile.name)

        # Now reach into each time edit, get its time value, and save it in an array.
        for i in range(self.SB.nPoints):
            self.up.set_position(self.SB.dataStart + i*self.SB.dataLen + stride)
            self.times.append(self.up.unpack_fp_scalar(self.SB.xtvRes))

        #print( self.times)
        if not self.times:
           raise XTVError(err_codes['TIME_EMPTY_ERR'], self.xtvFile.name)

        # The time lookups below use bisect, which assumes an ordered array.  The
        # comparison is non-strict so duplicate edits from a restart file pass.
        for i in range(1, len(self.times)):
            if self.times[i] < self.times[i-1]:
                raise XTVError(err_codes['TIME_ORDER_ERR'], self.xtvFile.name)

        #for comp in self.components.itervalues():
            #print( comp.fI)

        return


    def __parseChannelString(self, channel):
        """Given a TRACE XTV channel string, parse it into its constituent elements - channel name, component id,
           and mesh index information (radial, theta, axial).  All elements returned are strings.  Conversion to
           int is left to the caller as needed"""

        # Split the variable name from the rest of the string - we'll parse that separately

        (varName, sep, id_and_mesh) = channel.rpartition('-')  # use of rpartition gracefully handles dashes in the varName part

        # The following regex look really heinous b/c it uses named groups and unsaved groups.  The goal
        # is really to just extract the component ID and mesh indices from a string that looks something
        # like this:
        #
        #     "100A11R02T01"
        #
        # and accounting for the fact that the radial and theta parts are optional
        #
        #   (?P<id>                      : starts a named group called 'id'
        #          \d+                   : match one or more digits at the start of the string
        #   )                            : end the named group ('id')
        #   (?:                          : start an unsaved grouping
        #      A(?P<axial>               : match the letter A and start a named group called 'axial'
        #                 \d\d+          : match two or more digits
        #       )                        : end the named group ('axial')
        #      (?:                       : start an unsaved grouping
        #         R(?P<radial>           : match the letter R and start a named group called 'radial'
        #                     \d\d+      : match two or more digits
        #          )                     : end the named group ('radial')
        #         (?:                    : start another unsaved group
        #            T(?P<theta>         : match the letter T and start a named group called 'theta'
        #                       \d\d+    : match two or more digits
        #             )                  : end the named group ('theta')
        #         )?                     : end the unsaved group.  It can appear zero or one time
        #      )?                        : end the unsaved group.  It can appear zero or one time
        #   )?                           : end the unsaved group.  It can appear zero or one time

        #regex = r'(?P<id>\d+)A(?P<axial>\d\d+)(?:R(?P<radial>\d\d+)(?:T(?P<theta>\d\d+))?)?'
        regex = r'(?P<id>\d+)(?:A(?P<axial>\d\d+)(?:R(?P<radial>\d\d+)(?:T(?P<theta>\d\d+))?)?)?'
        p = re.compile(regex)

        try:
            m = p.match(id_and_mesh)
            compID = m.group('id')
            a = m.group('axial')
            r = m.group('radial')
            t = m.group('theta')
        except AttributeError:
            varName = channel
            compID = 0
            a = '0'
            r = '0'
            t = '0'

        return varName, compID, a, r, t


    def __decode_channel(self, channel):
        """Decodes an XTV data channel name into its constituent pieces.  Also identifies
           what component type the requested data channel belongs to.  Numbers denoting the
           component ID and mesh indices are returned as int, not strings"""

        # Parse the channel string into its constituent parts
        (varName, id, a, r, t) = self.__parseChannelString(channel)

        # Now figure out what the component type is for the data channel
        compType = self.__getCompType(id, varName)

        #if compType is None:
        #   raise XTVError(err_codes['INVALID_CHANNEL1'])

        # Convert mesh indices and comp ID to int
        id_int = int(id)
        try:
            a_int = int(a)
        except TypeError:
            a_int = None
        try:
            r_int = int(r)
        except TypeError:
            r_int = None
        try:
            t_int = int(t)
        except TypeError:
            t_int = None

        return varName, compType, id_int, a_int, r_int, t_int


    def __getCompType(self, compID, varName):
        """For a given channel identifier, retrieve the component type that
         corresponds to that channel"""

        compType = None
        tuples = self.components.keys()
        for (id,ct) in tuples:
            if id == int(compID):
                # The same compID can pair with both 'htstr' and 'htstrc' so we also need to check
                # the variable name to make sure we return the right component type string
                if self.components.get((id,ct)).channels.get(varName):
                    compType = ct
                    break
                else:
                    continue

        return compType


    def __getDimPosAt(self, id, compType, varName):
       """Private function for getting the dimPosAt attribute for a data channel of interest
          This has been isolated into its own function so that error trapping is isolated into one spot"""

       try:
           dim = self.components.get((id,compType)).channels.get(varName.strip()).dimPosAt.strip()
       except AttributeError:
           raise XTVError(err_codes['INVALID_CHANNEL1'])

       return dim


    def __transform_indices(self, id, compType, varName, xr, yt, z):
        """ Given a set of indices from an APTPlot data channel (A,R,T),
            convert them to typical i,j,k indices.  Unknown values of
            the component ID, component type, or variable name will raise
            an XTVError exception"""

        try:
            dimPosAt = self.__getDimPosAt(id, compType, varName)
        except XTVError as e:
           raise e

        if dimPosAt.startswith('3'):  # strings that start with a "3" indicate this is a 3D variable
            i = xr
            j = yt
            k = z
        elif dimPosAt.startswith('2'):
            i = xr
            j = z
            k = 0
            if yt is not None and yt > 0:
                raise XTVError(err_codes['ERR_YT_INDEX'])
        elif dimPosAt.startswith('1'):
            i = z
            j = 0
            k = 0
            if (yt is not None and yt > 0) or (xr is not None and xr > 0):
                raise XTVError(err_codes['ERR_XRYT_INDEX'])
        elif dimPosAt.startswith('0'):
            i = 0
            j = 0
            k = 0
            if (yt is not None and yt > 0) or (xr is not None and xr > 0) or (z is not None and z > 0):
                raise XTVError(err_codes['ERR_AXRYT_INDEX'])
        else:
            print("Programming Error - __transform_indices() has encountered a variable with a dimension it can't handle")
            raise ValueError

        return i, j, k


    def __interpolate(self, x1, y1, x2, y2, x):
        return (y2 - y1) * (x-x1)/(x2-x1) + y1


    def getAxialDataChannel(self, time, channel, zLoc):
        """
        Retrieves a single XTV value for a single data channel at a particular time and
        at a particular axial (z) location

        Args:
           time (float): the time point from which to retrieve a value.

           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

           zLoc (float): the axial location at which to retrieve the data value. This
           implies that the 'A' field in the channel string is irrelevant'.

        Returns:
           a single floating point value

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a single value at a particular axial location
               value = xtvObj.getAxialDataChannel(10.0, 'rftn-140A01R08', 2.45)

        """
        try:
            (varName, compType, id, a, r, t) = self.__decode_channel(channel)
            value = self.getAxialData(time, id, compType, varName, zLoc, r, t)
        except XTVError as e:
            raise e    # Don't trap any XTV-specific errors here - let the caller handle them

        return value


    def getDataChannel(self, time, channel):
        """
        Retrieves a single value at a particular time for a particular mesh index location

        Args:
           time (float): the time point from which to retrieve a value.

           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

        Returns:
           a single floating point value

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.


        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a single value at a particular point in time
               value = xtvObj.getDataChannel(50.0, 'pn-55A06')

        """

        try:
            (varName, compType, id, a, r, t) = self.__decode_channel(channel)
            (i,j,k) = self.__transform_indices(id, compType, varName, r, t, a)
            value = self.getData(time, id, compType, varName, i, j, k)
        except XTVError as e:
            raise e      # Don't trap any XTV-specific errors here - let the caller handle them

        return value


    def getAxialData(self, time, id, compType, varName, zLoc, xr=0, yt=0):
        """
        The purpose of this routine is to retrieve the value for a particular data channel
        at a particular axial location at a specific point in time

        Args:
           time (float): the time point from which to retrieve a value.

           id (int): the component number from which to retrieve a value

           compType (str): the component type from which to retrieve a value

           varName (str): the XTV variable name to retrieve

           zLoc (float): the axial location at which to retrieve the data value.

           xr (int): the radial/x coordinate index of the variable given by varName.  If
           varName does not have an X/R index, then it can be omitted.

           yt (int): the theta/y coordinate index of the variable given by varName.  If
           varName does not have a Y/T index, then it can be omitted.

        Returns:
           a single floating point value

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a single value at a particular axial location & point in time
               value = xtvObj.getAxialData(10.0, 140, 'htstrc', 'rftn', 2.45, 8)

        """

        # Knowing the z location we want to get a value for, we first need
        # to retrieve the i indices that bound that axial location at the two
        # time points that bound the desired time.  To do that, we will use the FI array.
        # It contains the z locations of each face.
        # If the variable is an edge variable, then we just use those values to interpolate.
        # If the variable is a cell center variable, then we first need to compute the cell center z values.
        # One question - what do we do for side legs in TEEs?  Looks like iFaces is just a construction from the dx array.


        # When the user selects a time value less than zero, that is a cue to use
        # the last time point, whatever it may be
        if time < 0:
            time = self.times[-1]
        elif time > self.times[-1]:      # caller has requested a time point beyond the last edit.
            raise XTVError(err_codes['TIME_UBOUND_ERR'])
        elif time < self.times[0]:      # caller has requested a time point before the first edit.
            raise XTVError(err_codes['TIME_LBOUND_ERR'])

        time_index = bisect.bisect_right(self.times, float(time))
        if time == self.times[time_index-1]:

            # First, we need to get the vector of axial heights
            zht = self.getAxialLocations(time, id, compType, varName)

            # Do some input checking
            if len(zht) == 0:   # list is empty.  Must be a scalar data channel
                raise XTVError(err_codes['AXIAL_SCALAR_ERR'])
            elif zLoc > zht[-1]:
                raise XTVError(err_codes['AXIAL_UBOUND_ERR'])
            elif zLoc < zht[0]:
                raise XTVError(err_codes['AXIAL_LBOUND_ERR'])

            # Get the lower index that bounds the requested height
            z = bisect.bisect_right(zht, float(zLoc))

            # If the requested height exactly matches axial location at z, then
            # we can use that index to retrieve the parameter of interest directly
            if zLoc == zht[z-1]:
                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z)
                value = self.getData(time, id, compType,varName,i,j,k)
            else:  # the requested height is between two axial levels.  Need to interpolate
                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z)
                lVal = self.getData(time, id, compType,varName,i,j,k)

                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z+1)
                uVal = self.getData(time, id, compType,varName,i,j,k)
                value = self.__interpolate(zht[z-1], lVal, zht[z], uVal, zLoc)

        else:
            timeLower = self.times[time_index-1]
            timeUpper = self.times[time_index]

            # Get the heights at the lower time bound
            zht = self.getAxialLocations(timeLower, id, compType, varName)

            # Do some input checking
            if len(zht) == 0:     # list is empty.  Must be a scalar data channel
                raise XTVError(err_codes['AXIAL_SCALAR_ERR'])
            elif zLoc > zht[-1]:
                raise XTVError(err_codes['AXIAL_UBOUND_ERR'])
            elif zLoc < zht[0]:
                raise XTVError(err_codes['AXIAL_LBOUND_ERR'])

            # Get the lower index that bounds the requested height
            z = bisect.bisect_right(zht, float(zLoc))

            # If the requested height exactly matches the axial location at z, then
            # we can use that index to retrieve the parameter of interest directly
            if zLoc == zht[z-1]:
                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z)
                lvalue = self.getData(timeLower, id, compType, varName, i, j, k)
            else:  # the requested height is between two axial levels.  Need to interpolate
                (i,j,k) = self.__transform_indices(id, compType, varName, xr, yt, z)
                lVal = self.getData(timeLower, id, compType, varName, i, j, k)

                (i,j,k) = self.__transform_indices(id, compType, varName, xr, yt, z+1)
                uVal = self.getData(timeLower, id, compType, varName, i, j, k)
                lvalue = self.__interpolate(zht[z-1], lVal, zht[z], uVal, zLoc)

            # Get the heights at the upper time bound
            zht = self.getAxialLocations(timeUpper, id, compType, varName)

            # Get the lower index that bounds the requested height
            z = bisect.bisect_right(zht, float(zLoc))

            # If the requested height exactly matches the axial location at z, then
            # we can use that index to retrieve the parameter of interest directly
            if zLoc == zht[z-1]:
                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z)
                uvalue = self.getData(timeUpper, id, compType, varName, i, j, k)
            else:  # the requested height is between two axial levels.  Need to interpolate
                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z)
                lVal = self.getData(timeUpper, id, compType, varName, i, j, k)

                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z+1)
                uVal = self.getData(timeUpper, id, compType, varName, i ,j ,k)
                uvalue = self.__interpolate(zht[z-1], lVal, zht[z], uVal, zLoc)

            # Now perform the final interpolation at the requested time.
            value = self.__interpolate(timeLower, lvalue, timeUpper, uvalue, time)

        return value


    def getAxialLocations(self, time, id, compType, varName):
        """
        The purpose of this routine is to retrieve the axial locations that
        correspond to a particular data channel for a given component at a
        particular point in time.  For all but fine mesh variables (whose
        node heights can vary with time), the time value is irrelevant and not
        used.

        Args:
           time (float): the time point from which to retrieve a value.

           id (int): the component number from which to retrieve a value

           compType (str): the component type from which to retrieve a value

           varName (str): the XTV variable name associated with the axial locations that
           should be retrieved.  Edge-based variables will retrieve face locations and
           cell-centered values will retrieve cell center locations.

        Returns:
           a list of floating point values

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve the fine mesh zht array for a htstr at 10.0 secs
               zht = xtvObj.getAxialLocations(10.0, 140, 'htstrc', 'rftn')

               # Retrieve the axial cell-center heights for a VESSEL cell-center variable
               cellHeights = xtvObj.getAxialLocations(0.0, 140, 'vessel', 'pn')

               # Retrieve the axial face heights for a VESSEL edge variable
               faceHeights = xtvObj.getAxialLocations(0.0, 140, 'vessel', 'vlnz')

        """

        #  There are few nuances.  For some data channels, the axial locations will correspond to
        #  cell centers, and for others, they will correspond to cell edges.

        #  For TEE-based components, the vector retrieved will include both the main tube and side tube.  It will be
        #  up to the calling routine to sort out what it wants

        #  For HTSTR components, some variables may be 2D.  For those variables, this routine will
        #  retrieve a 1D array of axial elevations/locations.  Some variables may be fine mesh variables.  In those
        #  cases, this routine will retrieve a vector of zht values that is only as large as needed.

        # For VESSEL components, only the z-direction elevation information will be retrieved.

        # If the variable is a 0D variable, then an empty list is returned.

        try:
            dimPosAt = self.__getDimPosAt(id, compType, varName)
        except XTVError as e:
           raise e

        tmplInd = self.components.get((id,compType)).channels.get(varName.strip()).vTmpl

        if dimPosAt == '0D':  # If the variable is a scalar, the entire notion of axial
            return []           # locations makes no sense, so just return an empty list

        zLocs = []
        if compType == 'htstrc':

            if dimPosAt == '1dFa':
                vLength = self.components.get((id,compType)).channels.get(varName).vLength
            elif dimPosAt == '2dFaJ':
                vLength = self.components.get((id,compType)).templates[tmplInd].dimj + 1
            else:
                print("Programming Error - unknown dimension for variable " + str(varName))
                exit()

            if varName in _fineMeshVars:
                cell = 1  # Need the entire array so start at the first mesh location
                startingPoint = self.components.get((id,compType)).channels.get('zht').startIncrement + (cell-1) * 4
                startingEdit = bisect.bisect_right(self.times, float(time))
                self.up.set_position(self.SB.dataStart + (startingEdit-1)*self.SB.dataLen + startingPoint)
                zLocs = self.up.unpack_fp_array(vLength, self.SB.xtvRes)
                zLocs = zLocs[0:zLocs.index(-1.0)]  # Filter out all the values that are unused
            else:
                zLocs = self.components.get((id,compType)).templates[tmplInd].fJ

        elif compType == 'htstr':
            zLocs = self.components.get((id,compType)).templates[tmplInd].fI[1:]

        elif  compType == 'vessel':
            zLocs = self.components.get((id,compType)).templates[tmplInd].fK
            if dimPosAt != '3dFaK':
                zLocs = [(zLocs[i]+zLocs[i+1])*0.5 for i in range(len(zLocs)-1)]   # Transform edge locations to cell center locations by calculating midpoints

        else:
            if dimPosAt.startswith('1'):
                zLocs = self.components.get((id,compType)).templates[tmplInd].fI
                if dimPosAt == '1dCc':
                    zLocs = [(zLocs[i]+zLocs[i+1])*0.5 for i in range(len(zLocs)-1)]   # Transform edge locations to cell center locations by calculating midpoints
            else:
                print("Programming Error - Not sure how to get axial locations for the requested variable " +  str(varName))
                exit()

        zLocs = [round(z,13) for z in zLocs]   # make sure floating point precision issues don't give us weird numbers
        return zLocs


    def getData(self, time, id, compType, varName, i=1, j=0, k=0):

        """
        Retrieves a value from the XTV file for a single point in
        time for a requested component ID, component type, & variable name. It
        works by first calculating the stride & file pointer offset for the
        variable of interest.  It then uses that offset to grab the value
        directly.

        Args:
           time (float): the time point from which to retrieve a value.

           id (int): the component number of the desired data channel

           compType (str): the component type of the desired data channel

           varName (str): the XTV variable name to retrieve

           i (int): the i-coordinate index of the data channel to be retrieved.  If
           varName does not have an i index, then it can be omitted.  In that case,
           a default value of 1 will be set.

           j (int): the j-coordinate index of the data channel to be retrieved.  If
           varName does not have a j index, then it can be omitted.

           k (int): the k-coordinate index of the data channel to be retrieved.  If
           varName does not have a k index, then it can be omitted.

        Returns:
           a single floating point value

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a single value at a particular point in time
               value = xtvObj.getData(10.0, 200, 'vessel', 'vln', 2, 3, 4)

        """

        try:
            dimPosAt = self.__getDimPosAt(id, compType, varName)
        except XTVError as e:   # if the component ID, component type or variable name are unknown, raise an exception
           raise e

        # Retrieve the dimensions associated with the current variable name.  If the variable has no
        # dimensions, it is likely a '0D' varName and we won't need them.  In that case, move on like
        # nothing happened.
        tmplInd = self.components.get((id,compType)).channels.get(varName.strip()).vTmpl
        vLength = self.components.get((id,compType)).channels.get(varName.strip()).vLength
        try:
            dimi = self.components.get((id,compType)).templates[tmplInd].dimi
            dimj = self.components.get((id,compType)).templates[tmplInd].dimj
            dimk = self.components.get((id,compType)).templates[tmplInd].dimk
        except AttributeError:
            dimi = None
            dimj = None
            dimk = None

        if dimPosAt.startswith('3'):  # strings that start with a "3" indicate this is a 3D variable

            if i is None or i == 0:
                raise XTVError(err_codes['INDEX_I_LBOUND_ERR'])
            if j is None or j == 0:
                raise XTVError(err_codes['INDEX_J_LBOUND_ERR'])
            if k is None or k == 0:
                raise XTVError(err_codes['INDEX_K_LBOUND_ERR'])

            coordSystem = self.components.get((id,compType)).templates[tmplInd].coordSys
            if dimPosAt.endswith("I"):  # strings that end with "I" correspond to XR face vectors
                nmesh_i = vLength/(dimj*dimk)
                nmesh_j = vLength/((dimi+1)*dimk)
                nmesh_k = vLength/((dimi+1)*dimj)
                levdim = (dimi+1) * dimj
                # the way the 3D cells are ordered in XTV is different from the way we typically count & label
                # them.  The calculation below reflects the fact that the radial index changes most rapidly,
                # whereas normal numbering would start counting by theta direction first, then by ring.
                cell = (k-1)*levdim + (j-1) * (dimi+1) + i
            elif dimPosAt.endswith("J"):  # strings that end with "J" correspond to YT face vectors
                if coordSystem == 'CYL3D':
                    nmesh_i = vLength/(dimj*dimk)
                    nmesh_j = vLength/(dimi*dimk)
                    nmesh_k = vLength/(dimi*dimj)
                    levdim = dimi * (dimj)
                else:
                    nmesh_i = vLength/((dimj+1)*dimk)
                    nmesh_j = vLength/(dimi*dimk)
                    nmesh_k = vLength/(dimi*(dimj+1))
                    levdim = dimi * (dimj+1)
                # the way the 3D cells are ordered in XTV is different from the way we typically count & label
                # them.  The calculation below reflects the fact that the radial index changes most rapidly,
                # whereas normal numbering would start counting by theta direction first, then by ring.
                cell = (k-1)*levdim + (j-1) * dimi + i
            elif dimPosAt.endswith("K"):  # strings that end with "K" correspond to Z face vectors
                nmesh_i = vLength/(dimj*(dimk+1))
                nmesh_j = vLength/(dimi*(dimk+1))
                nmesh_k = vLength/(dimi*dimj)
                levdim = dimi * dimj
                cell = (k-1)*levdim + (j-1) * dimi + i
            elif dimPosAt.endswith("c"):  # strings that end with "c" correspond to cell center
                nmesh_i = vLength/(dimj*dimk)
                nmesh_j = vLength/(dimi*dimk)
                nmesh_k = vLength/(dimi*dimj)
                levdim = dimi * dimj
                cell = (k-1)*levdim + (j-1) * dimi + i
            else:
                raise XTVError("!! Programming Error !! - Unexpected dimPosAt string")

            if i > nmesh_i:
                raise XTVError(err_codes['INDEX_I_UBOUND_ERR'])
            if j > nmesh_j:
                raise XTVError(err_codes['INDEX_J_UBOUND_ERR'])
            if k > nmesh_k:
                raise XTVError(err_codes['INDEX_K_UBOUND_ERR'])

        elif dimPosAt.startswith('2'):
            nmesh_i = vLength/(dimj+1)
            if i is None or i == 0:
                raise XTVError(err_codes['INDEX_I_LBOUND_ERR'])
            elif i > nmesh_i:
                raise XTVError(err_codes['INDEX_I_UBOUND_ERR'])

            nmesh_j = vLength/(dimi)
            if j is None or j == 0:
                raise XTVError(err_codes['INDEX_J_LBOUND_ERR'])
            elif j > nmesh_j:
                raise XTVError(err_codes['INDEX_J_UBOUND_ERR'])

            cell = (j-1) * dimi + i
        elif dimPosAt.startswith('1'):
            if i is None or i == 0:
                raise XTVError(err_codes['INDEX_I_LBOUND_ERR'])
            elif i > vLength:
                raise XTVError(err_codes['INDEX_I_UBOUND_ERR'])

            cell = i
        elif dimPosAt.startswith('0'):
            cell = 1
        else:
            raise XTVError("!! Programming Error !! - Encountered a variable with a dimension I can't handle")

        startingPoint = self.components.get((id,compType)).channels.get(varName).startIncrement + (cell-1) * self.SB.xtvRes

        # When the user selects a time value less than zero, that is a cue to use
        # the last time point, whatever it may be
        if time < 0:
            time = self.times[-1]
        elif time > self.times[-1]:      # caller has requested a time point beyond the last edit.
            raise XTVError(err_codes['TIME_UBOUND_ERR'])
        elif time < self.times[0]:      # caller has requested a time point before the first edit.
            raise XTVError(err_codes['TIME_LBOUND_ERR'])

        # If the requested time lines up exactly with the time of one of the
        # existing graphics edits, then just grab the value directly and get out.
        startingEdit = bisect.bisect_right(self.times, float(time))
        if time == self.times[startingEdit-1]:
            self.up.set_position(self.SB.dataStart + (startingEdit-1)*self.SB.dataLen + startingPoint)
            value = self.up.unpack_fp_scalar(self.SB.xtvRes)
            return value

        # Otherwise, grab the values at time points that bound the requested time and interpolate.
        self.up.set_position(self.SB.dataStart + (startingEdit-1)*self.SB.dataLen + startingPoint)
        y1 = self.up.unpack_fp_scalar(self.SB.xtvRes)
        self.up.set_position(self.SB.dataStart + startingEdit*self.SB.dataLen + startingPoint)
        y2 = self.up.unpack_fp_scalar(self.SB.xtvRes)
        value = self.__interpolate(self.times[startingEdit-1], y1, self.times[startingEdit], y2, time)
        return value
    
    def getTimeData(self, times, id, compType, varName, i=1, j=0, k=0):
        """
        Given a list of time points, retrieve a list of values from the XTV
        file for the requested component ID, component type, variable name,
        and mesh indices.

        Args:
           times (float): a list of the time points for which to retrieve
           an XTV variable.

           id (int): the component number of the desired data channel

           compType (str): the component type of the desired data channel

           varName (str): the XTV variable name to retrieve

           i (int): the i-coordinate index of the data channel to be retrieved.  If
           varName does not have an i index, then it can be omitted.  In that case,
           a default value of 1 will be set.

           j (int): the j-coordinate index of the data channel to be retrieved.  If
           varName does not have a j index, then it can be omitted.

           k (int): the k-coordinate index of the data channel to be retrieved.  If
           varName does not have a k index, then it can be omitted.

        Returns:
           a list of floats

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a list of values for a given list of time points
               time_points = [10.0, 20.0, 30.0]
               values = xtvObj.getTimeData(time_points, 200, 'vessel', 'vln', 2, 3, 4)

        """


        result = []
        for time in times:
            try:
                result.append(self.getData(time,id,compType,varName,i,j,k))
            except XTVError as e:
                raise e

        return result


    def getTimeVector(self, channel):
        """
        Returns a list of tuples of all the (time, value) pairs for a particular data channel

        Args:
           channel (str): the XTV data channel of interest, i.e. 'rftn-140A03R05'.

        Returns:
           a list of tuples of (time, value) pairs.  Values in tuples are floats.

        Raises:
           XTVError will be raised if the requested data channel is not available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a list of (time, value) pairs for a particular data channel
               list = xtvObj.getTimeVector('rftn-140A01R08')

        """

        (varName, compType, id, a, r, t) = self.__decode_channel(channel)
        (i,j,k) = self.__transform_indices(id, compType, varName, r, t, a)
        vector = []
        for time in self.times:
            value = self.getData(time, id, compType, varName, i, j, k)
            vector.append((time,value))
        return vector


    def getTimeVectorAxial(self, channel, zLoc):
        """
        Returns a list of tuples of all the (time, value) pairs for a particular
        data channel at a specific axial location.

        Args:
           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

           zLoc (float): the axial location at which to retrieve the data value. This
           implies that the 'A' field in the channel string is irrelevant.

        Returns:
           a list of tuples of (time, value) pairs.  Values in tuples are floats.

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a list of (time, value) pairs at a particular axial location
               list = xtvObj.getTimeVectorAxial('rftn-140A01R08', 4.5)

        """

        (varName, compType, id, a, xr, yt) = self.__decode_channel(channel)

        vector = []
        for time in self.times:

            # First, we need to get the vector of axial heights
            zLocs = self.getAxialLocations(time, id, compType, varName)

            # Get the lower index that bounds the requested height
            z = bisect.bisect_right(zLocs, float(zLoc))

            # If the requested height exactly matches axial location at z, then
            # we can use that index to retrieve the parameter of interest directly
            if zLoc == zLocs[z-1]:
                (i,j,k) = self.__transform_indices(id, compType, varName, xr, yt, z)
                value = self.getData(time, id, compType, varName, i, j, k)
            else:  # the requested height is between two axial levels.  Need to interpolate
                (i,j,k) = self.__transform_indices(id, compType, varName, xr, yt, z)
                lVal = self.getData(time, id, compType, varName, i, j, k)

                (i,j,k) = self.__transform_indices(id, compType,varName, xr, yt, z+1)
                uVal = self.getData(time, id, compType, varName, i, j, k)
                value = self.__interpolate(zLocs[z-1], lVal, zLocs[z], uVal, zLoc)

            vector.append((time,value))

        return vector


    def getAxialVector(self, time, channel):
        """
        Returns a list of tuples of all the (axial location, value) pairs for
        a particular data channel at a particular time

        Args:
           time (float): the time point from which to retrieve a value.

           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.
           In this case, the "A" index has no relevance to the values retrieved although error
           processing may still require it to have a valid value.

        Returns:
           a list of tuples of (z, value) pairs.  Values in tuples are floats.

        Raises:
           XTVError will be raised if the requested data channel or z location is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a list of (z location, value) pairs at a particular time
               list = xtvObj.getAxialVector(10.0, 'rftn-140A01R08')
        """

        (varName, compType, id, a, r, t) = self.__decode_channel(channel)
        #(i,j,k) = self.__transform_indices(id, compType, varName, r, t, a)
        zLocs = self.getAxialLocations(time, id, compType, varName)
        vector = []
        for z in zLocs:
            value = self.getAxialData(time, id, compType, varName, z, r, t)
            vector.append((z,value))
        return vector


    def getTimeChannel(self, times, channel):
        """
        Given a list of time points, retrieve a list of values from the XTV
        file for the requested XTV channel ID.

        Args:
           times (float): a list of the time points for which to retrieve
           an XTV variable.

           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

        Returns:
           a list of floats

        Raises:
           XTVError will be raised if the requested data channel is not
           available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a list of values for a given list of time points
               time_points = [10.0, 20.0, 30.0]
               values = xtvObj.getTimeChannel(time_points, 'rftn-140A03R05')

        """
        values = []
        (varName, compType, id, a, r, t) = self.__decode_channel(channel)
        (i,j,k) = self.__transform_indices(id, compType, varName, r, t, a)
        try:
          values = self.getTimeData(times, id, compType, varName, i, j, k)
        except XTVError as e:
           raise e

        return values

    def getUnits(self, channel):
        """
        Given an XTV data channel, retrieve a string that represents the units of
        the that data channel.

        Args:
           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

        Returns:
           a string that denotes the units of the data channel.  A value of 'unknown' is
           returned if the data channel is not found.

        Raises:
           XTVError will be raised if there is an error attempting to access the
           component and data channel metadata available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a string denoting the units of the given data channel
               units = xtvObj.getUnits('rftn-140A03R05')

        """
        units = 'unknown'
        (varName, compType, id, a, r, t) = self.__decode_channel(channel)
        try:
           cObj = self.components[(id,compType)]
           units = cObj.channels[varName].uLabel
        except XTVError as e:
           raise e

        return units


    def getDescription(self, channel):
        """
        Given an XTV data channel, retrieve a short description of the data channel

        Args:
           channel (str): a string of the XTV data channel of interest, i.e. 'rftn-140A03R05'.

        Returns:
           a string that denotes the description of the data channel.  A value of 'unknown' is
           returned if the data channel is not found.

        Raises:
           XTVError will be raised if there is an error attempting to access the
           component and data channel metadata available in the XTV file.

        Example::

           import xtvreader

           # Open the XTV fiie and save the filehandle
           xtv_file = "path/to/file.xtv"
           with open(xtv_file, 'rb') as xtvFileHandle:

               # Instantiate an XtvFile object with the open filehandle.  This will read
               # in and parse the XTV header information
               xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

               # Retrieve a string denoting the units of the given data channel
               units = xtvObj.getDescription('rftn-140A03R05')

        """
        desc = ' '
        (varName, compType, id, a, r, t) = self.__decode_channel(channel)
        try:
           cObj = self.components[(id,compType)]
           desc = cObj.channels[varName].varLabel
        except XTVError as e:
           raise e

        return desc


    def getList(self, list_all=False, with_desc=False):
       """
       Retrieve a dictionary containing the list of the available data channel
       names in the XTV file.

       Args:
          list_all (boolean): a logical flag denoting whether dictionary returned
          shall contain the full list of data channel names, or a more truncated list
          that only lists one value that corresponds to the maximum mesh location indices
          for each XTV variable.

          with_desc (boolean): a logical flag denoting whether the channel descriptions
          should be included along with the channel names in the returned dictionary.

       Returns:
          a dictionary of lists that contain the available XTV data channels for
          each component.  The keys of the dictionary correspond to the component type
          and component number.  They take the form <comp type>-<comp num>, so for
          example, "pipe-100", or "vessel-1", or "htstr-43001".  If the with_desc
          logical argument in True, then the returned entries in each list is 
          actually a tuple of the data channel ID and the associated description.

       Raises:
          XTVError will be raised if a data channel is found that has a dimension this
          function cannot handle.

       Example::

          import xtvreader

          # Open the XTV fiie and save the filehandle
          xtv_file = "path/to/file.xtv"
          with open(xtv_file, 'rb') as xtvFileHandle:

              # Instantiate an XtvFile object with the open filehandle.  This will read
              # in and parse the XTV header information
              xtvObj = xtvreader.XtvFile(xtvFileHandle, verbose=True)

              # Retrieve a list of values for a given list of time points
              xtvDict = xtvObj.getList(1)

       """

       def genXtvString(var, n=0, a=0, r=0, t=0):
         """ Generate an XTV data channel string from its constituent elements
         """
         if n == 0:
            string = str(var)
         elif a == 0:
            string = str(var) + "-" + str(n)
         elif r == 0:
            string = str(var) + "-" + str(n) + "A" + str(a).zfill(2)
         elif t == 0:
            string = str(var) + "-" + str(n) + "A" + str(a).zfill(2) + "R" + str(r).zfill(2)
         else:
            string = str(var) + "-" + str(n) + "A" + str(a).zfill(2) + "R" + str(r).zfill(2) + "T" + str(t).zfill(2)
         return string

       xtvChannels = {}
       for (id,compType), cObj in self.components.items():
         num = cObj.number
         if compType == 'htstrc':
            compType = 'htstr'
         comp_id = compType + "-" + str(id)
         xtvChannels.setdefault(comp_id, [])
         for chan in cObj.channels.values():
            dimPosAt = chan.dimPosAt.strip()
            tmplInd = chan.vTmpl
            vLength = chan.vLength
            desc = chan.varLabel
            if tmplInd > 0:
                coordSystem = cObj.templates[tmplInd].coordSys
                dimi = cObj.templates[tmplInd].dimi
                dimj = cObj.templates[tmplInd].dimj
                dimk = cObj.templates[tmplInd].dimk

            # Handle three-dimensional data channels    
            if dimPosAt.startswith('3'):  # strings that start with a "3" indicate this is a 3D variable
                if dimPosAt.endswith("I"):  # strings that end with "I" correspond to XR face vectors
                    nmesh_i = int(vLength/(dimj*dimk))
                    nmesh_j = int(vLength/((dimi+1)*dimk))
                    nmesh_k = int(vLength/((dimi+1)*dimj))
                elif dimPosAt.endswith("J"):  # strings that end with "J" correspond to YT face vectors
                    if coordSystem == 'CYL3D':
                        nmesh_i = int(vLength/(dimj*dimk))
                        nmesh_j = int(vLength/(dimi*dimk))
                        nmesh_k = int(vLength/(dimi*dimj))
                    else:
                        nmesh_i = int(vLength/((dimj+1)*dimk))
                        nmesh_j = int(vLength/(dimi*dimk))
                        nmesh_k = int(vLength/(dimi*(dimj+1)))
                elif dimPosAt.endswith("K"):  # strings that end with "K" correspond to Z face vectors
                    nmesh_i = int(vLength/(dimj*(dimk+1)))
                    nmesh_j = int(vLength/(dimi*(dimk+1)))
                    nmesh_k = int(vLength/(dimi*dimj))
                elif dimPosAt.endswith("c"):  # strings that end with "c" correspond to cell center
                    nmesh_i = int(vLength/(dimj*dimk))
                    nmesh_j = int(vLength/(dimi*dimk))
                    nmesh_k = int(vLength/(dimi*dimj))
                else:
                    raise XTVError("!! Programming Error !! - Unexpected dimPosAt string")
    
                if list_all:
                   for a in range(1, nmesh_k+1):
                      for r in range(1, nmesh_i+1):
                         for t in range(1, nmesh_j+1):
                            xtvChannel = genXtvString(chan.name, num, a, r, t)
                            if with_desc:
                               xtvChannel = (xtvChannel, desc)
                            xtvChannels[comp_id].append(xtvChannel)
                          
                else:
                   xtvChannel = genXtvString(chan.name, num, nmesh_k, nmesh_i, nmesh_j)
                   if with_desc:
                      xtvChannel = (xtvChannel, desc)
                   xtvChannels[comp_id].append(xtvChannel)

            # Handle two-dimensional data channels    
            elif dimPosAt.startswith('2'):
                nmesh_i = int(vLength/(dimj+1))
                nmesh_j = int(vLength/(dimi))
                if list_all:
                   for a in range(1, nmesh_j+1):
                      for r in range(1, nmesh_i+1):
                         xtvChannel = genXtvString(chan.name, num, a, r)
                         if with_desc:
                            xtvChannel = (xtvChannel, desc)
                         xtvChannels[comp_id].append(xtvChannel)
                else:
                   xtvChannel = genXtvString(chan.name, num, nmesh_j, nmesh_i)
                   if with_desc:
                      xtvChannel = (xtvChannel, desc)
                   xtvChannels[comp_id].append(xtvChannel)

            # Handle one-dimensional data channels    
            elif dimPosAt.startswith('1'):
                if list_all:
                   for a in range(1, chan.vLength+1):
                      xtvChannel = genXtvString(chan.name, num, a)
                      if with_desc:
                         xtvChannel = (xtvChannel, desc)
                      xtvChannels[comp_id].append(xtvChannel)
                          
                else:
                   xtvChannel = genXtvString(chan.name, num, chan.vLength)
                   if with_desc:
                      xtvChannel = (xtvChannel, desc)
                   xtvChannels[comp_id].append(xtvChannel)

            # Handle zero-dimensional data channels    
            elif dimPosAt.startswith('0'):
                xtvChannel = genXtvString(chan.name, num)
                if with_desc:
                   xtvChannel = (xtvChannel, desc)
                xtvChannels[comp_id].append(xtvChannel)
            else:
                raise XTVError("!! Programming Error !! - Encountered a variable with a dimension I can't handle")

       return xtvChannels


###############################################################################
# EVERYTHING BELOW THIS LINE IS USED FOR TESTING THE CLASSES & ROUTINES ABOVE
################################################################################

# Dictionary of XTV files used in the test routines below.  Using the keys of this
# dictionary gives us a shorthand way of referencing a particular file we want to use
# to extract variables from
xtvFiles = {
            'xtv1':     r"./TestData/w4loopnewHS.xtv",
            'xtv1_64':  r"./TestData/w4loopnewHS.xtv64", # double precision version of the xtv file
            'xtv2':     r"./TestData/VessXYZ.xtv",
            'xtv2_64':  r"./TestData/VessXYZ.xtv64",     # double precision version of the xtv file
            'xtv3':     r"./TestData/VessXYZ_rst.xtv",
            'xtv4':     r"./TestData/PumpTyp8.Rev1R.xtv",  # corrupted XTV file to test an error msg
           }

# Dictionary of lists of the expected time points in each XTV file provided in the
# dictionary above.  Some of our tests rely on knowing the exact time point value
# given in the XTV file.  If the XTV files are ever regenerated with a newer code
# version these time points could change slightly.  We can use this dictionary to
# compare to the time points we find in the XTV files we actually read to ensure our
# tests are always as-designed
expected_times = {
                  # These times correspond to having run ./TestData/w4loopnewHS.inp with V5.1147
                  'xtv1': [0.0, 1.0809282064437866, 2.186551332473755, 3.2496180534362793,
                           4.314905643463135, 5.3850016593933105, 6.457952976226807,
                           7.532965183258057, 8.610002517700195, 9.689167022705078,
                           20.11336326599121, 30.14255714416504, 40.1854133605957],
                  # These times correspond to having run ./TestData/w4loopnewHS.inp with V5.1255
                  'xtv1_64' : [0.0, 1.0809281794113679, 2.187699598216114, 3.2517302482991597,
                               4.317728822191254, 5.388394561107987, 6.461875439570398,
                               7.537392929075994, 8.614903313809444, 9.694500671695629,
                               20.120849776493912, 30.150675110070825, 40.193720237622465],
                  # These times correspond to having run ./TestData/VessXYZ.inp with V5.1147
                  'xtv2': [0.0, 5.014662265777588, 10.055557250976562,
                           15.097119331359863, 20.138683319091797],
                  # These times correspond to having run ./TestData/VessXYZ.inp with V5.1255
                  'xtv2_64': [0.0, 5.01466206337293, 10.05555721732118,
                              15.09711946965819, 20.13868266167558],
                  # These times correspond to having run ./TestData/VessXYZ_rst.inp with V5.1147
                  'xtv3': [10.0, 15.003966331481934, 20.045530319213867],
                  # This xtv file is corrupted so it has no state data or time points
                  'xtv4': [],
                 }


def tests(tests_to_run):
    """Main driver routine for performing XTV read tests"""

    print
    print("*********************")
    print("Running Index Tests  ")
    print("*********************")
    test_index(tests_to_run)

    print
    print("********************")
    print("Running Axial Tests  ")
    print("********************")
    test_axial(tests_to_run)

    print
    print("********************")
    print("Running Error Tests  ")
    print("********************")
    test_errors(tests_to_run)

    print
    print("**************************")
    print("Running Time Vector Tests  ")
    print("**************************")
    test_timeVector(tests_to_run)

    print
    print("********************************")
    print("Running Time Vector Axial Tests  ")
    print("********************************")
    test_timeVectorAxial(tests_to_run)

    print
    print("***************************")
    print("Running Axial Vector Tests  ")
    print("***************************")
    test_axialVector(tests_to_run)

    print
    print("****************")
    print("Tests complete")
    print("****************")
    return


def test_axial(tests_to_run):
    """Driver routine used to test the getAxialDataChannel() and
       getAxialData() functions.  Results are compared to the
       output of the pypost utility"""

    # Attempts to retrieve XTV data at specific axial distances
    tests = {
             # dict key: (xtv_file_key, time, channel, z location)                        # Dictionary template

             'a_pipe01': ('xtv1',10.0,'vln-12A01',0.0),                                   # edge-based variable at bottom face
             'a_pipe02': ('xtv1',2.5,'rlmf-12A01',15.2),                                  # edge-based variable at top face
             'a_pipe03': ('xtv1',2.5,'rlmf-12A01',6.5),                                   # edge-based variable at an internal face
             'a_pipe04': ('xtv1',expected_times['xtv1'][5],'rlmf-12A01',6.1),             # edge-based variable between 2 internal faces
             'a_pipe05': ('xtv1',10.0,'roln-12A01',2.0),                                  # cell-based variable at bottom cell center
             'a_pipe06': ('xtv1',2.5,'roln-12A01',13.025),                                # cell-based variable at top cell center
             'a_pipe07': ('xtv1',2.5,'el-12A01',6.0),                                     # cell-based variable at internal cell center
             'a_pipe08': ('xtv1',expected_times['xtv1'][5],'el-12A01',5.5),               # cell-based variable between 2 internal cell centers
             # double precision versions of selected xtv tests from above
             'a_pipe09': ('xtv1_64',2.5,'rlmf-12A01',6.5),                                # edge-based variable at an internal face
             'a_pipe10': ('xtv1_64',expected_times['xtv1_64'][5],'rlmf-12A01',6.1),       # edge-based variable between 2 internal faces
             'a_pipe11': ('xtv1_64',2.5,'el-12A01',6.0),                                  # cell-based variable at internal cell center
             'a_pipe12': ('xtv1_64',expected_times['xtv1_64'][5],'el-12A01',5.5),         # cell-based variable between 2 internal cell centers

             'a_htstr01': ('xtv1',expected_times['xtv1'][2],'tClad-140A01',0.0303525),    # coarse mesh var at bottom coarse node row
             'a_htstr02': ('xtv1',7.0,'tClad-140A01',0.6374025),                          # coarse mesh var at internal coarse node row
             'a_htstr03': ('xtv1',10.0,'tClad-140A01',1.0),                               # coarse mesh var between 2 internal coarse node rows
             'a_htstr04': ('xtv1',2.0,'heatingR-140A01',3.6120475),                       # coarse mesh var at top coarse node row
             'a_htstr05': ('xtv1',expected_times['xtv1'][4],'tsurfo-140A01',0.0),         # perm fine mesh var at bottom node row
             'a_htstr06': ('xtv1',4.0,'tsurfo-140A01',1.09269),                           # perm fine mesh var at internal node row
             'a_htstr07': ('xtv1',5.0,'tsurfo-140A01',1.2),                               # perm fine mesh var between 2 internal node rows
             'a_htstr08': ('xtv1',20.2,'tsurfo-140A01',3.6424),                           # perm fine mesh var at top node row
             'a_htstrc01': ('xtv1',expected_times['xtv1'][2],'zht-140A01',0.0),           # 1D dynamic fine mesh var at bottom node row
             'a_htstrc02': ('xtv1',expected_times['xtv1'][2],'zht-140A01',1.09269),       # 1D dynamic fine mesh var at internal node row
             'a_htstrc03': ('xtv1',expected_times['xtv1'][2],'zht-140A01',2.314),         # 1D dynamic fine mesh var between 2 internal node rows
             'a_htstrc04': ('xtv1',expected_times['xtv1'][2],'zht-140A01',3.6424),        # 1D dynamic fine mesh var at top node row
             'a_htstrc05': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R08',0.0),       # 2D dynamic fine mesh var at bottom node row
             'a_htstrc06': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R08',1.09269),   # 2D dynamic fine mesh var at internal node row
             'a_htstrc07': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R08',2.314),     # 2D dynamic fine mesh var between 2 internal node rows
             'a_htstrc08': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R08',3.6424),    # 2D dynamic fine mesh var at top node row
             # double precision versions of selected xtv tests from above
             'a_htstr09': ('xtv1_64',7.0,'tClad-140A01',0.6374025),                          # coarse mesh var at internal coarse node row
             'a_htstr10': ('xtv1_64',10.0,'tClad-140A01',1.0),                               # coarse mesh var between 2 internal coarse node rows
             'a_htstr11': ('xtv1_64',4.0,'tsurfo-140A01',1.09269),                           # perm fine mesh var at internal node row
             'a_htstr12': ('xtv1_64',5.0,'tsurfo-140A01',1.2),                               # perm fine mesh var between 2 internal node rows
             'a_htstrc09': ('xtv1_64',expected_times['xtv1_64'][2],'zht-140A01',1.09269),       # 1D dynamic fine mesh var at internal node row
             'a_htstrc10': ('xtv1_64',expected_times['xtv1_64'][2],'zht-140A01',2.314),         # 1D dynamic fine mesh var between 2 internal node rows
             'a_htstrc11': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A01R08',1.09269),   # 2D dynamic fine mesh var at internal node row
             'a_htstrc12': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A01R08',2.314),     # 2D dynamic fine mesh var between 2 internal node rows

             'a_tee01': ('xtv1',10.0,'vln-10A01', 0.0),                                   # edge-based variable at bottom face of a TEE
             'a_tee02': ('xtv1',10.0,'vln-10A01', 31.8226),                               # edge-based variable at top face of a TEE
             # double precision versions of selected xtv tests from above
             'a_tee03': ('xtv1_64',10.0,'vln-10A01', 31.8226),                            # edge-based variable at top face of a TEE

             # Cell-based variables
             'a_vessel01': ('xtv1',6.0,'tln-26A01R01T01',0.895),                          # cell-based variable at bottom cell
             'a_vessel02': ('xtv1',6.0,'tln-26A01R02T03',4.7962),                         # cell-based variable at internal cell
             'a_vessel03': ('xtv1',6.0,'tln-26A01R01T04',5.5),                            # cell-based variable between 2 cells between 2 time points
             'a_vessel04': ('xtv1',expected_times['xtv1'][6],'tln-26A01R01T02',5.5),      # cell-based variable between 2 cells at exact time point
             'a_vessel05': ('xtv1',6.0,'tln-26A01R02T04',11.665),                         # cell-based variable at top cell of a Vessel

             # Edge-based variables in the Z component direction
             'a_vessel06': ('xtv1',6.0,'vlnz-26A01R01T01',0.0),                           # edge-based Z variable at bottom face
             'a_vessel07': ('xtv1',6.0,'vlnz-26A01R02T01',4.1891),                        # edge-based Z variable at internal face
             'a_vessel08': ('xtv1',6.0,'vlnz-26A01R01T04',5.2),                           # edge-based Z variable between 2 faces between 2 time points
             'a_vessel09': ('xtv1',expected_times['xtv1'][6],'vlnz-26A01R01T02',5.2),     # edge-based Z variable between 2 faces at exact time point
             'a_vessel10': ('xtv1',6.0,'vlnz-26A01R02T04',12.51),                         # edge-based Z variable at top face of a Vessel

             # Edge-based variables in the XR component direction
             'a_vessel11': ('xtv1',6.0,'vlnxr-26A01R01T01',0.895),                        # edge-based XR variable at the innermost radial face, bottom axial cell (value should be zero)
             'a_vessel12': ('xtv1',6.0,'vlnxr-26A01R01T03',4.7962),                       # edge-based XR variable at the innermost radial face, middle axial cell (value should be zero)
             'a_vessel13': ('xtv1',6.0,'vlnxr-26A01R01T04',5.2),                          # edge-based XR variable at the innermost radial face, between 2 cells, between 2 time points (value should be zero)
             'a_vessel14': ('xtv1',expected_times['xtv1'][6],'vlnxr-26A01R01T02',5.2),    # edge-based XR variable at the innermost radial face, between 2 cells, at exact time point (value should be zero)
             'a_vessel15': ('xtv1',6.0,'vlnxr-26A01R01T04',11.665),                       # edge-based XR variable at the innermost radial face, top axial cell (value should be zero)
             'a_vessel16': ('xtv1',6.0,'vlnxr-26A01R02T01',0.895),                        # edge-based XR variable at internal radial face, bottom axial cell
             'a_vessel17': ('xtv1',6.0,'vlnxr-26A01R02T03',4.7962),                       # edge-based XR variable at internal radial face, middle axial cell
             'a_vessel18': ('xtv1',6.0,'vlnxr-26A01R02T04',5.2),                          # edge-based XR variable at internal radial face, between 2 cells, between 2 time points
             'a_vessel19': ('xtv1',expected_times['xtv1'][6],'vlnxr-26A01R02T02',5.2),    # edge-based XR variable at internal radial face, between 2 cells, at exact time point
             'a_vessel20': ('xtv1',6.0,'vlnxr-26A01R02T04',11.665),                       # edge-based XR variable at internal radial face, top axial cell
             'a_vessel21': ('xtv1',6.0,'vlnxr-26A01R03T01',0.895),                        # edge-based XR variable at the outermost radial face, bottom axial cell (value should be zero)
             'a_vessel22': ('xtv1',6.0,'vlnxr-26A01R03T03',4.7962),                       # edge-based XR variable at the outermost radial face, middle axial cell (value should be zero)
             'a_vessel23': ('xtv1',6.0,'vlnxr-26A01R03T04',5.2),                          # edge-based XR variable at the outermost radial face, between 2 cells, between 2 time points (value should be zero)
             'a_vessel24': ('xtv1',expected_times['xtv1'][6],'vlnxr-26A01R03T02',5.2),    # edge-based XR variable at the outermost radial face, between 2 cells, at exact time point (value should be zero)
             'a_vessel25': ('xtv1',6.0,'vlnxr-26A01R03T04',11.665),                       # edge-based XR variable at the outermost radial face, top axial cell (value should be zero)

             # Edge-based variables in the YT component direction
             'a_vessel26': ('xtv1',6.0,'vlnyt-26A01R01T01',0.895),                        # edge-based YT variable at the first theta face, bottom axial cell
             'a_vessel27': ('xtv1',6.0,'vlnyt-26A01R02T01',4.7962),                       # edge-based YT variable at the first theta face, middle axial cell
             'a_vessel28': ('xtv1',6.0,'vlnyt-26A01R01T01',5.2),                          # edge-based YT variable at the first theta face, between 2 cells between 2 time points
             'a_vessel29': ('xtv1',expected_times['xtv1'][6],'vlnyt-26A01R01T01',5.2),    # edge-based YT variable at the first theta face, between 2 cells at exact time point
             'a_vessel30': ('xtv1',6.0,'vlnyt-26A01R02T01',11.665),                       # edge-based YT variable at the first theta face, top axial cell
             'a_vessel31': ('xtv1',6.0,'vlnyt-26A01R01T02',0.895),                        # edge-based YT variable at internal theta face, bottom axial cell
             'a_vessel32': ('xtv1',6.0,'vlnyt-26A01R02T02',4.7962),                       # edge-based YT variable at internal theta face, middle axial cell
             'a_vessel33': ('xtv1',6.0,'vlnyt-26A01R01T02',5.2),                          # edge-based YT variable at internal theta face, between 2 cells between 2 time points
             'a_vessel34': ('xtv1',expected_times['xtv1'][6],'vlnyt-26A01R01T02',5.2),    # edge-based YT variable at internal theta face, between 2 cells at exact time point
             'a_vessel35': ('xtv1',6.0,'vlnyt-26A01R02T02',11.665),                       # edge-based YT variable at internal theta face, top axial cell
             'a_vessel36': ('xtv1',6.0,'vlnyt-26A01R01T04',0.895),                        # edge-based YT variable at last theta face, bottom axial cell
             'a_vessel37': ('xtv1',6.0,'vlnyt-26A01R02T04',4.7962),                       # edge-based YT variable at last theta face, middle axial cell
             'a_vessel38': ('xtv1',6.0,'vlnyt-26A01R01T04',5.2),                          # edge-based YT variable at last theta face, between 2 cells between 2 time points
             'a_vessel39': ('xtv1',expected_times['xtv1'][6],'vlnyt-26A01R01T04',5.2),    # edge-based YT variable at last theta face, between 2 cells at exact time point
             'a_vessel40': ('xtv1',6.0,'vlnyt-26A01R02T04',11.665),                       # edge-based YT variable at last theta face, top axial cell
             # double precision versions of selected xtv tests from above
             'a_vessel41': ('xtv1_64',6.0,'tln-26A01R02T03',4.7962),                         # cell-based variable at internal cell
             'a_vessel42': ('xtv1_64',expected_times['xtv1_64'][6],'tln-26A01R01T02',5.5),   # cell-based variable between 2 cells at exact time point
             'a_vessel43': ('xtv1_64',6.0,'vlnz-26A01R02T01',4.1891),                        # edge-based Z variable at internal face
             'a_vessel44': ('xtv1_64',expected_times['xtv1_64'][6],'vlnxr-26A01R02T02',5.2), # edge-based XR variable at internal radial face, between 2 cells, at exact time point
             'a_vessel45': ('xtv1_64',6.0,'vlnyt-26A01R01T02',5.2),                          # edge-based YT variable at internal theta face, between 2 cells between 2 time points

             'a_plen01': ('xtv1',2.0,'pStag-16A01',0.5),                                  # cell-based variable in plenum
             'a_plen02': ('xtv1_64',2.0,'pStag-16A01',0.5),                               # cell-based variable in plenum

             'a_prizr01': ('xtv1',0.5,'slpratio-19A01',0.0),                              # edge-based variable at lower edge of prizer
             'a_prizr02': ('xtv1',4.0,'slpratio-19A01',6.0107),                           # edge-based variable at internal edge of prizer
             'a_prizr03': ('xtv1',4.0,'slpratio-19A01',8.0),                              # edge-based variable between 2 edges of prizer
             'a_prizr04': ('xtv1',4.0,'slpratio-19A01',14.9),                             # edge-based variable at top edge of prizer
             'a_prizr05': ('xtv1',3.0,'el-19A01',3.00535),                                # cell-based variable at bottom of prizer
             'a_prizr06': ('xtv1',0.5,'el-19A03',11.5),                                   # cell-based variable internal to prizer
             'a_prizr07': ('xtv1',0.5,'el-19A03',14.8),                                   # cell-based variable at top of prizer

             'a_fill01': ('xtv1',12.0,'tln-8A01',0.5),                                    # cell-based variable in fill
             'a_fill02': ('xtv1',2.0,'vvn-8A01',0.0),                                     # edge-based variable in fill, first edge
             'a_fill03': ('xtv1',5.0,'vvn-8A01',1.0),                                     # edge-based variable in fill, second edge
             # double precision versions of selected xtv tests from above
             'a_fill04': ('xtv1_64',12.0,'tln-8A01',0.5),                                 # cell-based variable in fill
             'a_fill05': ('xtv1_64',2.0,'vvn-8A01',0.0),                                  # edge-based variable in fill, first edge

             'a_break01': ('xtv1',2.0,'tln-7A01',0.05),                                   # cell-based variable in break
             # double precision versions of selected xtv tests from above
             'a_break02': ('xtv1_64',2.0,'tln-7A01',0.05),                                # cell-based variable in break

            }

    # The following lists are here for reference only so as to assist with creating new test cases
    pipe12_face_elevations =   [0.0,    4.0,     5.5,    6.5,      10.85,      15.2]
    pipe12_center_elevations = [    2.0,    4.75,    6.0,    8.675,      13.025     ]

    vessel26_face_elevations =   [0.0,  1.79,  2.975,  4.1891,  5.4033,  6.6174,  10.82,  12.51]
    vessel26_center_elevations = [  0.895, 2.3825, 3.58205, 4.7962, 6.01035, 8.7187,  11.665,  ]

    htstr140_coarse_elevs = [0.030352499999999998, 0.6374025, 1.8212000000000002,
                             3.0049975, 3.6120475]
    htstr140_permFM_elevs = [0.0, 0.15176249999999997, 0.36422999999999994,
                             0.6070499999999999, 0.8498699999999999, 1.09269,
                             1.3355199999999998, 1.5783599999999998, 1.8211999999999997,
                             2.06404, 2.30688, 2.54971, 2.79253, 3.03535,
                             3.2781700000000003, 3.4906375, 3.6424]

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")

        (xtv_key, time, channel, zLoc) = tests[test]

        if xtv_key in xtvOpenFiles:
            pass
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)

        xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")

        try:
            myValues[test] = xtvFile.getAxialDataChannel(time, channel, zLoc)
        except XTVError as e:
            writeErrorMessage('getAxialDataChannel', channel, time, zLoc, errmsg=e)
            myValues[test] = None

        try:
            expectedValues[test] = pypost_getZValue(test, xtvFiles[xtv_key], channel, time, zLoc)
        except subprocess.CalledProcessError as e:
            writeErrorMessage('pyPost', channel, time, zLoc, errmsg=e)
            expectedValues[test] = None

        checkResult(test, myValues[test], expectedValues[test])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def test_index(tests_to_run):
    """Driver routine used to test the getDataChannel() and getData()
       functions.  Results are compared to the output of the pypost
       utility"""

    # Attempts to retrieve XTV data at specific mesh indices
    tests = {
             # dict key: (xtv_file_key, time, channel)                       # Dictionary template

             #############################
             # 1D component data requests
             #############################

             'i_pipe01': ('xtv1',5.0,'vln-12A01'),                           # edge-based variable at bottom face
             'i_pipe02': ('xtv1',5.0,'vln-12A06'),                           # edge-based variable at top face
             'i_pipe03': ('xtv1',5.0,'vln-12A03'),                           # edge-based variable at internal face
             'i_pipe04': ('xtv1',2.0,'pn-12A01'),                            # cell-based variable at bottom cell
             'i_pipe05': ('xtv1',2.0,'pn-12A05'),                            # cell-based variable at top cell
             'i_pipe06': ('xtv1',2.0,'pn-12A02'),                            # cell-based variable at internal cell
             'i_pipe07': ('xtv1',5.0,'cmass-12'),                            # component-based scalar variable
             # double precision versions of selected xtv tests from above
             'i_pipe07': ('xtv1_64',5.0,'vln-12A03'),                        # edge-based variable at internal face
             'i_pipe08': ('xtv1_64',2.0,'pn-12A02'),                         # cell-based variable at internal cell
             'i_pipe09': ('xtv1_64',5.0,'cmass-12'),                         # component-based scalar variable

             'i_tee01': ('xtv1',15.0,'rhofj-10A01'),                         # edge-based variable at bottom face of main tube
             'i_tee02': ('xtv1',15.0,'rhofj-10A05'),                         # edge-based variable at top face of main tube
             'i_tee03': ('xtv1',15.0,'rhofj-10A01'),                         # edge-based variable at internal face of main tube
             'i_tee04': ('xtv1',15.0,'rhofj-10A06'),                         # edge-based variable at bottom face of side tube
             'i_tee05': ('xtv1',15.0,'rhofj-10A08'),                         # edge-based variable at top face of side tube
             'i_tee06': ('xtv1',15.0,'rhofj-10A07'),                         # edge-based variable at internal face of side tube
             'i_tee07': ('xtv1',5.0,'cl-10A01'),                             # cell-based variable at bottom cell of main tube
             'i_tee08': ('xtv1',5.0,'cl-10A04'),                             # cell-based variable at top cell of main tube
             'i_tee09': ('xtv1',5.0,'cl-10A02'),                             # cell-based variable at jcell of main tube
             'i_tee10': ('xtv1',5.0,'cl-10A05'),                             # cell-based variable at ghost cell of TEE
             'i_tee11': ('xtv1',5.0,'cl-10A06'),                             # cell-based variable at bottom cell of side tube
             'i_tee12': ('xtv1',5.0,'cl-10A07'),                             # cell-based variable at top cell of side tube
             # double precision versions of selected xtv tests from above
             'i_tee13': ('xtv1_64',15.0,'rhofj-10A01'),                      # edge-based variable at internal face of main tube
             'i_tee14': ('xtv1_64',15.0,'rhofj-10A07'),                      # edge-based variable at internal face of side tube
             'i_tee15': ('xtv1_64',5.0,'cl-10A02'),                          # cell-based variable at jcell of main tube
             'i_tee16': ('xtv1_64',5.0,'cl-10A05'),                          # cell-based variable at ghost cell of TEE

             'i_plen01': ('xtv1',2.0,'pStag-16A01'),                         # cell-based variable in plenum
             'i_plen02': ('xtv1',2.0,'massE-16'),                            # scalar variable in plenum
             # double precision versions of selected xtv tests from above
             'i_plen03': ('xtv1_64',2.0,'pStag-16A01'),                      # cell-based variable in plenum
             'i_plen04': ('xtv1_64',2.0,'massE-16'),                         # scalar variable in plenum

             'i_prizr01': ('xtv1',0.5,'vln-19A01'),                          # edge-based variable at lower edge of prizer
             'i_prizr02': ('xtv1',4.0,'vln-19A04'),                          # edge-based variable at top edge of prizer
             'i_prizr03': ('xtv1',5.0,'massE-19'),                           # scalar variable in prizer
             'i_prizr04': ('xtv1',3.0,'el-19A01'),                           # cell-based variable at bottom of prizer
             'i_prizr05': ('xtv1',0.5,'el-19A03'),                           # cell-based variable at bottom of prizer

             'i_fill01': ('xtv1',12.0,'tln-8A01'),                           # cell-based variable in fill
             'i_fill02': ('xtv1',2.0,'vvn-8A01'),                            # edge-based variable in fill, first edge
             'i_fill03': ('xtv1',5.0,'vvn-8A02'),                            # edge-based variable in fill, second edge
             'i_fill04': ('xtv1',12.0,'massC-8'),                            # scalar variable in fill
             # double precision versions of selected xtv tests from above
             'i_fill05': ('xtv1_64',12.0,'tln-8A01'),                        # cell-based variable in fill
             'i_fill06': ('xtv1_64',2.0,'vvn-8A01'),                         # edge-based variable in fill, first edge
             'i_fill07': ('xtv1_64',5.0,'vvn-8A02'),                         # edge-based variable in fill, second edge
             'i_fill08': ('xtv1_64',12.0,'massC-8'),                         # scalar variable in fill

             'i_break01': ('xtv1',2.0,'tln-7A01'),                           # cell-based variable in break
             'i_break02': ('xtv1',2.0,'enth-7'),                             # scalar variable in break
             # double precision versions of selected xtv tests from above
             'i_break03': ('xtv1_64',2.0,'tln-7A01'),                        # cell-based variable in break
             'i_break04': ('xtv1_64',2.0,'enth-7'),                          # scalar variable in break

             ###################################
             # Cylindrical vessel data requests
             ###################################

             # Edge-based variables in the XR component direction
             'i_vessel01': ('xtv1',30.0,'cixr-26A01R01T01'),                 # edge-based XR variable at the innermost radial face, bottom axial level (value should be zero)
             'i_vessel02': ('xtv1',30.0,'cixr-26A03R01T03'),                 # edge-based XR variable at the innermost radial face, middle axial level (value should be zero)
             'i_vessel03': ('xtv1',30.0,'cixr-26A07R01T04'),                 # edge-based XR variable at the innermost radial face, top axial level (value should be zero)
             'i_vessel04': ('xtv1',11.0,'cixr-26A01R02T04'),                 # edge-based XR variable at internal radial face of last theta cell and bottom axial cell
             'i_vessel05': ('xtv1',5.0,'cixr-26A04R02T03'),                  # edge-based XR variable at internal radial face of middle theta cell and middle axial cell
             'i_vessel06': ('xtv1',27.0,'cixr-26A07R02T01'),                 # edge-based XR variable at internal radial face of first theta cell and top axial cell
             'i_vessel07': ('xtv1',11.0,'cixr-26A01R03T04'),                 # edge-based XR variable at outermost radial face, bottom axial cell (value should be zero)
             'i_vessel08': ('xtv1',5.0,'cixr-26A04R03T03'),                  # edge-based XR variable at outermost radial face, middle axial cell (value should be zero)
             'i_vessel09': ('xtv1',27.0,'cixr-26A07R03T01'),                 # edge-based XR variable at outermost radial face, top axial cell (value should be zero)

             # Edge-based variables in the YT component direction
             'i_vessel10': ('xtv1',2.0,'ciyt-26A01R01T01'),                  # edge-based YT variable at the first theta face, bottom axial level
             'i_vessel11': ('xtv1',1.7,'ciyt-26A03R02T01'),                  # edge-based YT variable at the first theta face, middle axial level
             'i_vessel12': ('xtv1',1.7,'ciyt-26A07R01T01'),                  # edge-based YT variable at the first theta face, top axial level
             'i_vessel13': ('xtv1',3.0,'ciyt-26A01R02T02'),                  # edge-based YT variable at internal theta face of bottom axial cell
             'i_vessel14': ('xtv1',6.0,'ciyt-26A04R01T02'),                  # edge-based YT variable at internal theta face of middle axial cell
             'i_vessel15': ('xtv1',1.5,'ciyt-26A07R02T02'),                  # edge-based YT variable at internal theta face of top axial cell
             'i_vessel16': ('xtv1',1.5,'ciyt-26A01R02T04'),                  # edge-based YT variable at last theta face, bottom axial cell
             'i_vessel17': ('xtv1',2.0,'ciyt-26A03R01T04'),                  # edge-based YT variable at last theta face, middle axial cell
             'i_vessel18': ('xtv1',5.5,'ciyt-26A07R02T04'),                  # edge-based YT variable at last theta face, top axial cell

             # Edge-based variables in the Z component direction
             'i_vessel19': ('xtv1',0.0,'mmflz-26A01R01T01'),                 # edge-based Z variable at the bottom axial level, 1st cell, first ring (should be zero)
             'i_vessel20': ('xtv1',9.0,'mmflz-26A01R02T02'),                 # edge-based Z variable at the bottom axial level, internal cell, 2nd ring (should be zero)
             'i_vessel21': ('xtv1',30.0,'mmflz-26A01R02T04'),                # edge-based Z variable at the bottom axial level, last cell, 2nd ring (should be zero)
             'i_vessel22': ('xtv1',9.2,'mmflz-26A04R01T01'),                 # edge-based Z variable at the middle axial cell, 1st cell, 1st ring
             'i_vessel23': ('xtv1',4.0,'mmflz-26A04R02T02'),                 # edge-based Z variable at the middle axial cell, internal cell, 2nd ring
             'i_vessel24': ('xtv1',1.5,'mmflz-26A04R02T04'),                 # edge-based Z variable at the middle axial cell, last cell, 2nd ring
             'i_vessel25': ('xtv1',1.5,'mmflz-26A08R01T01'),                 # edge-based Z variable at the top axial cell, 1st cell, first ring (should be zero)
             'i_vessel26': ('xtv1',5.0,'mmflz-26A08R02T02'),                 # edge-based Z variable at the top axial cell, internal cell, 2nd ring (should be zero)
             'i_vessel27': ('xtv1',9.5,'mmflz-26A08R02T04'),                 # edge-based Z variable at the top axial cell, last cell, 2nd ring (should be zero)

             'i_vessel28': ('xtv1',0.5,'vlnxr-26A06R02T02'),                 # edge-based XR variable at some arbitrarily chosen internal face

             'i_vessel29': ('xtv1',1.7,'pn-26A01R01T01'),                    # cell-based variable at bottom axial cell, 1st cell, 1st ring
             'i_vessel30': ('xtv1',6.0,'pn-26A01R02T01'),                    # cell-based variable at bottom axial cell, 1st cell, 2nd ring
             'i_vessel31': ('xtv1',8.1,'vlvol-26A01R02T04'),                 # cell-based variable at bottom axial cell, last cell, 2nd ring
             'i_vessel32': ('xtv1',7.5,'roln-26A03R01T01'),                  # cell-based variable at middle axial cell, 1st cell, 1st ring
             'i_vessel33': ('xtv1',5.0,'roln-26A03R02T01'),                  # cell-based variable at middle axial cell, 1st cell, 2nd ring
             'i_vessel34': ('xtv1',5.0,'roln-26A03R02T04'),                  # cell-based variable at middle axial cell, last cell, 2nd ring
             'i_vessel35': ('xtv1',7.5,'pn-26A07R01T01'),                    # cell-based variable at top axial cell, 1st cell, 1st ring
             'i_vessel36': ('xtv1',7.5,'roln-26A07R02T01'),                  # cell-based variable at top axial cell, 1st cell, 2nd ring
             'i_vessel37': ('xtv1',20.0,'vlvol-26A07R02T04'),                # cell-based variable at top axial cell, last cell, 2nd ring

             'i_vessel38': ('xtv1',34.5,'tcilmf-26'),                        # scalar variable

             #################################
             # Cartesian vessel data requests
             #################################

             # Edge-based variables in the XR component direction
             'i_vessel39': ('xtv2',2.5,'vlnxr-3A01R01T01'),                  # edge-based XR variable at the innermost radial face, bottom axial level (value should be zero)
             'i_vessel40': ('xtv2',2.5,'vlnxr-3A02R01T02'),                  # edge-based XR variable at the innermost radial face, middle axial level (value should be zero)
             'i_vessel41': ('xtv2',2.5,'vlnxr-3A03R01T03'),                  # edge-based XR variable at the innermost radial face, top axial level (value should be zero)
             'i_vessel42': ('xtv2',2.5,'vlnxr-3A01R02T03'),                  # edge-based XR variable at internal radial face of last theta cell and bottom axial cell
             'i_vessel43': ('xtv2',2.5,'vlnxr-3A02R02T02'),                  # edge-based XR variable at internal radial face of middle theta cell and middle axial cell
             'i_vessel44': ('xtv2',2.5,'vlnxr-3A03R02T01'),                  # edge-based XR variable at internal radial face of first theta cell and top axial cell
             'i_vessel45': ('xtv2',2.5,'vlnxr-3A01R04T01'),                  # edge-based XR variable at outermost radial face, bottom axial cell (value should be zero)
             'i_vessel46': ('xtv2',2.5,'vlnxr-3A02R04T02'),                  # edge-based XR variable at outermost radial face, middle axial cell (value should be zero)
             'i_vessel47': ('xtv2',2.5,'vlnxr-3A03R04T03'),                  # edge-based XR variable at outermost radial face, top axial cell (value should be zero)

             # Edge-based variables in the YT component direction
             'i_vessel48': ('xtv2',2.5,'vlnyt-3A01R01T01'),                  # edge-based YT variable at the first theta face, bottom axial level
             'i_vessel49': ('xtv2',2.5,'vlnyt-3A02R02T01'),                  # edge-based YT variable at the first theta face, middle axial level
             'i_vessel50': ('xtv2',2.5,'vlnyt-3A03R03T01'),                  # edge-based YT variable at the first theta face, top axial level
             'i_vessel51': ('xtv2',2.5,'vlnyt-3A01R01T02'),                  # edge-based YT variable at internal theta face of bottom axial cell
             'i_vessel52': ('xtv2',2.5,'vlnyt-3A02R02T02'),                  # edge-based YT variable at internal theta face of middle axial cell
             'i_vessel53': ('xtv2',2.5,'vlnyt-3A03R03T02'),                  # edge-based YT variable at internal theta face of top axial cell
             'i_vessel54': ('xtv2',2.5,'vlnyt-3A01R01T04'),                  # edge-based YT variable at last theta face, bottom axial cell
             'i_vessel55': ('xtv2',2.5,'vlnyt-3A02R02T04'),                  # edge-based YT variable at last theta face, middle axial cell
             'i_vessel56': ('xtv2',2.5,'vlnyt-3A03R03T04'),                  # edge-based YT variable at last theta face, top axial cell

             # Edge-based variables in the Z component direction
             'i_vessel57': ('xtv2',2.5,'vlnz-3A01R01T01'),                   # edge-based Z variable at the bottom axial level, 1st cell, first ring (should be zero)
             'i_vessel58': ('xtv2',2.5,'vlnz-3A01R02T02'),                   # edge-based Z variable at the bottom axial level, internal cell, 2nd ring (should be zero)
             'i_vessel59': ('xtv2',2.5,'vlnz-3A01R02T03'),                   # edge-based Z variable at the bottom axial level, last cell, 2nd ring (should be zero)
             'i_vessel60': ('xtv2',2.5,'vlnz-3A02R01T01'),                   # edge-based Z variable at the middle axial cell, 1st cell, 1st ring
             'i_vessel61': ('xtv2',2.5,'vlnz-3A02R02T02'),                   # edge-based Z variable at the middle axial cell, internal cell, 2nd ring
             'i_vessel62': ('xtv2',2.5,'vlnz-3A02R02T03'),                   # edge-based Z variable at the middle axial cell, last cell, 2nd ring
             'i_vessel63': ('xtv2',2.5,'vlnz-3A04R01T01'),                   # edge-based Z variable at the top axial cell, 1st cell, first ring (should be zero)
             'i_vessel64': ('xtv2',2.5,'vlnz-3A04R02T02'),                   # edge-based Z variable at the top axial cell, internal cell, 2nd ring (should be zero)
             'i_vessel65': ('xtv2',2.5,'vlnz-3A04R02T03'),                   # edge-based Z variable at the top axial cell, last cell, 2nd ring (should be zero)

             # double precision versions of selected xtv tests from above
             'i_vessel66': ('xtv1_64',30.0,'cixr-26A03R01T03'),              # edge-based XR variable at the innermost radial face, middle axial level (value should be zero)
             'i_vessel67': ('xtv1_64',5.0,'cixr-26A04R02T03'),               # edge-based XR variable at internal radial face of middle theta cell and middle axial cell
             'i_vessel68': ('xtv1_64',5.0,'cixr-26A04R03T03'),               # edge-based XR variable at outermost radial face, middle axial cell (value should be zero)
             'i_vessel69': ('xtv1_64',1.7,'ciyt-26A03R02T01'),               # edge-based YT variable at the first theta face, middle axial level
             'i_vessel70': ('xtv1_64',6.0,'ciyt-26A04R01T02'),               # edge-based YT variable at internal theta face of middle axial cell
             'i_vessel71': ('xtv1_64',2.0,'ciyt-26A03R01T04'),               # edge-based YT variable at last theta face, middle axial cell
             'i_vessel72': ('xtv1_64',9.0,'mmflz-26A01R02T02'),              # edge-based Z variable at the bottom axial level, internal cell, 2nd ring (should be zero)
             'i_vessel73': ('xtv1_64',4.0,'mmflz-26A04R02T02'),              # edge-based Z variable at the middle axial cell, internal cell, 2nd ring
             'i_vessel74': ('xtv1_64',5.0,'mmflz-26A08R02T02'),              # edge-based Z variable at the top axial cell, internal cell, 2nd ring (should be zero)
             'i_vessel75': ('xtv1_64',0.5,'vlnxr-26A06R02T02'),              # edge-based XR variable at some arbitrarily chosen internal face
             'i_vessel76': ('xtv1_64',7.5,'roln-26A03R01T01'),               # cell-based variable at middle axial cell, 1st cell, 1st ring
             'i_vessel77': ('xtv1_64',5.0,'roln-26A03R02T01'),               # cell-based variable at middle axial cell, 1st cell, 2nd ring
             'i_vessel78': ('xtv1_64',5.0,'roln-26A03R02T04'),               # cell-based variable at middle axial cell, last cell, 2nd ring
             'i_vessel79': ('xtv1_64',34.5,'tcilmf-26'),                     # scalar variable
             'i_vessel80': ('xtv2_64',2.5,'vlnxr-3A02R01T02'),               # edge-based XR variable at the innermost radial face, middle axial level (value should be zero)
             'i_vessel81': ('xtv2_64',2.5,'vlnxr-3A02R02T02'),               # edge-based XR variable at internal radial face of middle theta cell and middle axial cell
             'i_vessel82': ('xtv2_64',2.5,'vlnxr-3A02R04T02'),               # edge-based XR variable at outermost radial face, middle axial cell (value should be zero)
             'i_vessel83': ('xtv2_64',2.5,'vlnyt-3A02R02T01'),               # edge-based YT variable at the first theta face, middle axial level
             'i_vessel84': ('xtv2_64',2.5,'vlnyt-3A02R02T02'),               # edge-based YT variable at internal theta face of middle axial cell
             'i_vessel85': ('xtv2_64',2.5,'vlnyt-3A02R02T04'),               # edge-based YT variable at last theta face, middle axial cell
             'i_vessel86': ('xtv2_64',2.5,'vlnz-3A01R02T02'),                # edge-based Z variable at the bottom axial level, internal cell, 2nd ring (should be zero)
             'i_vessel87': ('xtv2_64',2.5,'vlnz-3A02R02T02'),                # edge-based Z variable at the middle axial cell, internal cell, 2nd ring
             'i_vessel88': ('xtv2_64',2.5,'vlnz-3A04R02T02'),                # edge-based Z variable at the top axial cell, internal cell, 2nd ring (should be zero)

             ##############################
             # POWER & HTSTR data requests
             ##############################

             # Power Variables
             'i_power01': ('xtv1',34.5,'fuelAvgT-174'),                      # scalar variable
             'i_power02': ('xtv1',0.6,'pctAvg-6002'),                        # scalar variable
             # double precision versions of selected xtv tests from above
             'i_power03': ('xtv1_64',34.5,'fuelAvgT-174'),                   # scalar variable
             'i_power04': ('xtv1_64',0.6,'pctAvg-6002'),                     # scalar variable

             # HTSTR Variables
             'i_htstr01': ('xtv1',2.1,'tClad-140A01'),                       # coarse mesh var at bottom coarse node row
             'i_htstr02': ('xtv1',7.0,'tClad-140A03'),                       # coarse mesh var at internal coarse node row
             'i_htstr03': ('xtv1',2.0,'heatingR-140A05'),                    # coarse mesh var at top coarse node row
             'i_htstr04': ('xtv1',4.3,'tsurfo-140A01'),                      # perm fine mesh var at bottom node row
             'i_htstr05': ('xtv1',4.0,'tsurfo-140A08'),                      # perm fine mesh var at internal node row
             'i_htstr06': ('xtv1',20.2,'tsurfo-140A17'),                     # perm fine mesh var at top node row
             'i_htstr07': ('xtv1',2.1,'avgFuelT-140'),                       # scalar
             # double precision versions of selected xtv tests from above
             'i_htstr08': ('xtv1_64',7.0,'tClad-140A03'),                    # coarse mesh var at internal coarse node row
             'i_htstr09': ('xtv1_64',4.0,'tsurfo-140A08'),                   # perm fine mesh var at internal node row
             'i_htstr10': ('xtv1_64',2.1,'avgFuelT-140'),                    # scalar

             'i_htstrc01': ('xtv1',expected_times['xtv1'][2],'tchfo-140A01'),            # 1D dynamic fine mesh var at bottom node row
             'i_htstrc02': ('xtv1',expected_times['xtv1'][2],'tchfo-140A11'),            # 1D dynamic fine mesh var at internal node row
             'i_htstrc03': ('xtv1',expected_times['xtv1'][2],'tchfo-140A17'),            # 1D dynamic fine mesh var at top node row
             'i_htstrc04': ('xtv1',expected_times['xtv1'][2],'tchfo-140A18'),            # 1D dynamic fine mesh var, one level above top node row
             'i_htstrc05': ('xtv1',expected_times['xtv1'][2],'tchfo-140A100'),           # 1D dynamic fine mesh var at last open storage location
             'i_htstrc06': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R01'),          # 2D dynamic fine mesh var at bottom node row, inner node
             'i_htstrc07': ('xtv1',expected_times['xtv1'][2],'rftn-140A01R08'),          # 2D dynamic fine mesh var at bottom node row, outer node
             'i_htstrc08': ('xtv1',expected_times['xtv1'][2],'rftn-140A06R01'),          # 2D dynamic fine mesh var at internal node row, inner node
             'i_htstrc09': ('xtv1',expected_times['xtv1'][2],'rftn-140A06R08'),          # 2D dynamic fine mesh var at internal node row, outer node
             'i_htstrc10': ('xtv1',expected_times['xtv1'][2],'rftn-140A17R01'),          # 2D dynamic fine mesh var at top node row, inner node
             'i_htstrc11': ('xtv1',expected_times['xtv1'][2],'rftn-140A17R08'),          # 2D dynamic fine mesh var at top node row, outer node
             'i_htstrc12': ('xtv1',expected_times['xtv1'][2],'rftn-140A18R08'),          # 2D dynamic fine mesh var, one level above top node row
             'i_htstrc13': ('xtv1',expected_times['xtv1'][2],'rftn-140A100R08'),         # 2D dynamic fine mesh var at last open storage location
             'i_htstrc14': ('xtv1',2.1,'pLosso-140'),                                    # scalar
             # double precision versions of selected xtv tests from above
             'i_htstrc15': ('xtv1_64',expected_times['xtv1_64'][2],'tchfo-140A11'),      # 1D dynamic fine mesh var at internal node row
             'i_htstrc16': ('xtv1_64',expected_times['xtv1_64'][2],'tchfo-140A18'),      # 1D dynamic fine mesh var, one level above top node row
             'i_htstrc17': ('xtv1_64',expected_times['xtv1_64'][2],'tchfo-140A100'),     # 1D dynamic fine mesh var at last open storage location
             'i_htstrc18': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A06R01'),    # 2D dynamic fine mesh var at internal node row, inner node
             'i_htstrc19': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A06R08'),    # 2D dynamic fine mesh var at internal node row, outer node
             'i_htstrc20': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A18R08'),    # 2D dynamic fine mesh var, one level above top node row
             'i_htstrc21': ('xtv1_64',expected_times['xtv1_64'][2],'rftn-140A100R08'),   # 2D dynamic fine mesh var at last open storage location
             'i_htstrc22': ('xtv1_64',2.1,'pLosso-140'),                                 # scalar

             #########################################
             # General & control system data requests
             #########################################

             # General Variables
             'i_general01': ('xtv1',5.0,'massE'),                            # general variable, no component association
             'i_general02': ('xtv1',1.5,'dtvmax'),                           # general variable, no component association
             'i_general03': ('xtv1',0.5,'mNCGE'),                            # general variable, no component association
             # double precision versions of selected xtv tests from above
             'i_general04': ('xtv1_64',5.0,'massE'),                         # general variable, no component association
             'i_general05': ('xtv1_64',1.5,'dtvmax'),                        # general variable, no component association
             'i_general06': ('xtv1_64',0.5,'mNCGE'),                         # general variable, no component association

             # Control System Parameters
             'i_control01': ('xtv1',7.2,'cb99003'),                          # control block
             'i_control02': ('xtv1',7.2,'sv4'),                              # signal variable
             'i_control03': ('xtv1',4.3,'sv99004'),                          # signal variable
             'i_control04': ('xtv1',5.0,'tp11'),                             # trip
             # double precision versions of selected xtv tests from above
             'i_control05': ('xtv1_64',7.2,'cb99003'),                       # control block
             'i_control06': ('xtv1_64',7.2,'sv4'),                           # signal variable
             'i_control07': ('xtv1_64',4.3,'sv99004'),                       # signal variable
             'i_control08': ('xtv1_64',5.0,'tp11'),                          # trip
            }

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")

        (xtv_key, time, channel) = tests[test]

        if xtv_key in xtvOpenFiles:
            pass
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)

        xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")


        try:
            myValues[test] = xtvFile.getDataChannel(time, channel)
        except XTVError as e:
            writeErrorMessage('getDataChannel', channel, time, errmsg=e)
            myValues[test] = None

        try:
            expectedValues[test] = pypost_getSingleValue(test, xtvFiles[xtv_key], channel, time)
        except subprocess.CalledProcessError as e:
            writeErrorMessage('pyPost', channel, time, errmsg=e)
            expectedValues[test] = None

        checkResult(test, myValues[test], expectedValues[test])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def test_errors(tests_to_run):
    """Driver routine used to test the error trapping functionality of the
       XTVReader functions when they are fed invalid or improper data.  The
       error messages produced are compared to the error messages we expect
       to be produced for a given situation"""

    # Attempts to retrieve XTV data at specific axial distances
    tests = {
             # dict key: (xtv_file_key, time, channel, zLoc, expected_error_code       # Dictionary template

             'err01': ('xtv1',45.0,'vln-12A01','TIME_UBOUND_ERR'),                # beyond last time point
             'err02': ('xtv3',4.0,'tln-4A01','TIME_LBOUND_ERR'),                 # before first time point where first time point > 0.0s (as could happen on a restart)

             'err03': ('xtv1',0.0,'xxx-12A01','INVALID_CHANNEL1'),               # variable that does not exist
             'err04': ('xtv1',0.0,'fuelavgt-174','INVALID_CHANNEL1'),            # data channel name that does not have the proper capitalization
             'err05': ('xtv1',0.0,'vln-777A01','INVALID_CHANNEL1'),              # component ID that does not exist

             # Attempts to retrieve values from mesh indices that do not exist
             'err06': ('xtv1',0.0,'vln-12A07','INDEX_I_UBOUND_ERR'),             # edge-based variable beyond last face index
             'err07': ('xtv1',0.0,'pn-12A06','INDEX_I_UBOUND_ERR'),              # cell-based variable beyond last cell index
             'err08': ('xtv1',0.0,'pn-12A00','INDEX_I_LBOUND_ERR'),              # cell-based variable at node 00

             # Heat structure related requests
             'err09': ('xtv1',0.0,'rftn-140A101R08','INDEX_J_UBOUND_ERR'),       # 2D fine mesh variable beyond last node row
             'err10': ('xtv1',0.0,'rftn-140A01R09','INDEX_I_UBOUND_ERR'),        # 2D fine mesh variable beyond last radial node
             'err11': ('xtv1',0.0,'rftn-140A01R00','INDEX_I_LBOUND_ERR'),        # 2D fine mesh variable at radial node 00
             'err12': ('xtv1',0.0,'rftn-140A01','INDEX_I_LBOUND_ERR'),           # 2D fine mesh variable but only A field provided
             'err13': ('xtv1',0.0,'qchfo-140A101','INDEX_I_UBOUND_ERR'),         # 1D fine mesh var above top node row
             'err14': ('xtv1',0.0,'qchfo-140A01R01','ERR_XRYT_INDEX'),           # 1D fine mesh var but 2D fields provided
             'err15': ('xtv1',0.0,'tsurfo-140A18','INDEX_I_UBOUND_ERR'),         # perm fine mesh var above top node row
             'err16': ('xtv1',0.0,'heatingR-140A06','INDEX_I_UBOUND_ERR'),       # coarse mesh var above top node row
             'err17': ('xtv1',0.0,'avgFuelT-140A01','ERR_AXRYT_INDEX'),          # Attempt to retrieve a 0D value at a particular mesh location

             # Invalid vessel index requests
             'err18': ('xtv1',0.0,'cixr-26A01R04T01','INDEX_I_UBOUND_ERR'),      # edge-based XR variable at an invalid radial face
             'err19': ('xtv1',0.0,'cixr-26A01R01T05','INDEX_J_UBOUND_ERR'),      # edge-based XR variable at an invalid theta cell
             'err20': ('xtv1',0.0,'cixr-26A08R01T01','INDEX_K_UBOUND_ERR'),      # edge-based XR variable at an invalid axial level
             'err21': ('xtv1',0.0,'ciyt-26A01R03T01','INDEX_I_UBOUND_ERR'),      # edge-based YT variable at an invalid radial face
             'err22': ('xtv1',0.0,'ciyt-26A01R01T05','INDEX_J_UBOUND_ERR'),      # edge-based YT variable at an invalid theta cell
             'err23': ('xtv1',0.0,'ciyt-26A08R01T01','INDEX_K_UBOUND_ERR'),      # edge-based YT variable at an invalid axial level
             'err24': ('xtv1',0.0,'ciz-26A01R03T01','INDEX_I_UBOUND_ERR'),       # edge-based Z variable at an invalid radial face
             'err25': ('xtv1',0.0,'ciz-26A01R01T05','INDEX_J_UBOUND_ERR'),       # edge-based Z variable at an invalid theta cell
             'err26': ('xtv1',0.0,'ciz-26A09R01T01','INDEX_K_UBOUND_ERR'),       # edge-based Z variable at an invalid axial level
             'err27': ('xtv1',0.0,'pn-26A01R03T01','INDEX_I_UBOUND_ERR'),        # cell-based variable at an invalid radial face
             'err28': ('xtv1',0.0,'pn-26A01R01T05','INDEX_J_UBOUND_ERR'),        # cell-based variable at an invalid theta cell
             'err29': ('xtv1',0.0,'pn-26A08R01T01','INDEX_K_UBOUND_ERR'),        # cell-based variable at an invalid axial level

             # Invalid General and control system
             'err30': ('xtv1',0.0,'avgFuelT','INVALID_CHANNEL1'),                # invalid general variable
             'err31': ('xtv1',0.0,'sv5','INVALID_CHANNEL1'),                     # invalid signal variable
             'err32': ('xtv1',0.0,'cb3000','INVALID_CHANNEL1'),                  # invalid control block
             'err33': ('xtv1',0.0,'tr16','INVALID_CHANNEL1'),                    # invalid trip request

             # Invalid axial height requests for 1D components
             'err34': ('xtv1',0.0,'roln-12A01',13.5,'AXIAL_UBOUND_ERR'),         # 1D cell-centered value at axial location above the top-most cell center
             'err35': ('xtv1',0.0,'roln-12A01',1.0,'AXIAL_LBOUND_ERR'),          # 1D cell-centered value at axial location below the bottom-most cell center
             'err36': ('xtv1',0.0,'rlmf-12A01',15.3,'AXIAL_UBOUND_ERR'),         # 1D face-centered value at axial location beyond the top-most face
             'err37': ('xtv1',0.0,'vln-12A01',-1.0,'AXIAL_LBOUND_ERR'),          # 1D face-centered value at axial location below the bottom-most face (0.0 m)

             # Invalid axial height requests for HTSTR components
             'err38': ('xtv1',0.0,'heatingR-140A06', 0.01,'AXIAL_LBOUND_ERR'),   # HS coarse mesh var at z location below bottom node row
             'err39': ('xtv1',0.0,'heatingR-140A06', 3.62,'AXIAL_UBOUND_ERR'),   # HS coarse mesh var at z location above top node row
             'err40': ('xtv1',0.0,'tsurfo-140A06', -1.0,'AXIAL_LBOUND_ERR'),     # HS perm fine mesh var at z location below bottom node row
             'err41': ('xtv1',0.0,'tsurfo-140A06', 4.0,'AXIAL_UBOUND_ERR'),      # HS perm fine mesh var at z location above top node row
             'err42': ('xtv1',0.0,'rftn-140A06R08', -1.0, 'AXIAL_LBOUND_ERR'),   # HS dyn fine mesh var at z location below bottom node row
             'err43': ('xtv1',0.0,'rftn-140A06R08', 4.0,'AXIAL_UBOUND_ERR'),     # HS dyn fine mesh var at z location above top node row

             # Invalid axial height requests for scalar, general and control system variables
             'err44': ('xtv1',0.0,'cmass-12',1.0,'AXIAL_SCALAR_ERR'),            # axial request for scalar variable in a 1D component
             'err45': ('xtv1',0.0,'tcolmf-26',1.0,'AXIAL_SCALAR_ERR'),           # axial request for scalar variable in a VESSEL
             'err46': ('xtv1',0.0,'fuelAvgT-174',1.0,'AXIAL_SCALAR_ERR'),        # axial request for scalar variable in POWER
             'err47': ('xtv1',0.0,'avgFuelT-140',1.0,'AXIAL_SCALAR_ERR'),        # axial request for scalar variable in HTSTR
             'err48': ('xtv1',0.0,'massE',1.0,'AXIAL_SCALAR_ERR'),               # axial request for general variable
             'err49': ('xtv1',0.0,'cb99003',1.0,'AXIAL_SCALAR_ERR'),             # axial request for control block
             'err50': ('xtv1',0.0,'sv99004',1.0,'AXIAL_SCALAR_ERR'),             # axial request for signal variable
             'err51': ('xtv1',0.0,'tp11',1.0,'AXIAL_SCALAR_ERR'),                # axial request for trip

             'err52': ('xtv1',2.0,'pStag-16A01',0.2,'AXIAL_LBOUND_ERR'),         # axial request for plenum variable below cell
             'err53': ('xtv1',2.0,'pStag-16A01',0.7,'AXIAL_UBOUND_ERR'),         # axial request for plenum variable above cell
             'err54': ('xtv1',2.0,'massE-16',0.5,'AXIAL_SCALAR_ERR'),            # axial request for scalar variable in plenum

             'err55': ('xtv1',0.5,'vln-19A01',-1.0,'AXIAL_LBOUND_ERR'),          # axial request for edge-based variable below lower edge of prizer
             'err56': ('xtv1',4.0,'vln-19A01',15.0,'AXIAL_UBOUND_ERR'),          # axial request for edge-based variable above top edge of prizer
             'err57': ('xtv1',5.0,'massE-19',5.0,'AXIAL_SCALAR_ERR'),            # axial request for scalar variable in prizer
             'err58': ('xtv1',3.0,'el-19A01',1.0,'AXIAL_LBOUND_ERR'),            # axial request for cell-based variable below bottom cell
             'err59': ('xtv1',0.5,'el-19A01',15.0,'AXIAL_UBOUND_ERR'),           # axial request for cell-based variable above top cell

             'err60': ('xtv1',12.0,'tln-8A01',0.4,'AXIAL_LBOUND_ERR'),           # axial request for cell-based fill variable below bottom cell
             'err61': ('xtv1',12.0,'tln-8A01',0.6,'AXIAL_UBOUND_ERR'),           # axial request for cell-based fill variable above top cell
             'err62': ('xtv1',2.0,'vvn-8A01',-0.5,'AXIAL_LBOUND_ERR'),           # axial request for edge-based fill variable below bottom edge
             'err63': ('xtv1',5.0,'vvn-8A02',1.1,'AXIAL_UBOUND_ERR'),            # axial request for edge-based fill variable above top edge
             'err64': ('xtv1',12.0,'massC-8',0.5,'AXIAL_SCALAR_ERR'),            # axial request for scalar variable in fill

             'err65': ('xtv1',2.0,'tln-7A01',0.04,'AXIAL_LBOUND_ERR'),           # axial request in break below cell center
             'err66': ('xtv1',2.0,'tln-7A01',0.06,'AXIAL_UBOUND_ERR'),           # axial request in break above cell center
             'err67': ('xtv1',2.0,'enth-7',0.5,'AXIAL_SCALAR_ERR'),              # axial request for scalar variable in break

             'err68': ('xtv4',0.0,'alpn-182A01','TIME_EMPTY_ERR'),                # beyond last time point
             # Criteria specified as integers
             #'err44': ('xtv1',1,'vln-12A01',''),                 # time value specified as an integer
             #'err45': ('xtv1',0.0,'roln-12A01',2,''),            # z location specified as an integer

            }

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")


        if len(tests[test]) == 5:
            (xtv_key, time, channel, zLoc, expected_errCode) = tests[test]
        else:
            zLoc = None
            (xtv_key, time, channel, expected_errCode) = tests[test]


        if xtv_key in xtvOpenFiles:
            xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            try:
               xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)
               myValues[test] = xtvOpenFiles[xtv_key]
            except XTVError as xtverr:  # trap exceptions raised while trying to open and parse the XTV file
               writeErrorMessage('XtvFile', channel, time, errmsg=xtverr.args[0])
               myValues[test] = xtverr.args[0]
               checkErrMsg(test, myValues[test], err_codes[expected_errCode])
               continue   # skip to the next test
            except Exception as e:
               print( traceback.format_exc())
               myValues[test] = e
            else:
               xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")

        if zLoc is None:
            try:
                myValues[test] = xtvFile.getDataChannel(time, channel)
            except XTVError as xtverr:
                writeErrorMessage('getDataChannel', channel, time, errmsg=xtverr)
                myValues[test] = xtverr.args[0]
            except Exception as e:
                print( traceback.format_exc())
                myValues[test] = e

        else:
            try:
                myValues[test] = xtvFile.getAxialDataChannel(time, channel, zLoc)
            except XTVError as xtverr:
                writeErrorMessage('getAxialDataChannel', channel, time, zLoc, errmsg=xtverr)
                myValues[test] = xtverr.args[0]
            except Exception as e:
                print( traceback.format_exc())
                myValues[test] = e

        checkErrMsg(test, myValues[test], err_codes[expected_errCode])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def test_timeVector(tests_to_run):
    """Driver routine used to test the getTimeVector() function.  Results are
       compared to the output of the pypost utility"""

    # The test matrix for this function is not as extensive as test_index() because
    # getTimeVector() is really just a wrapper around calls to getData.  Since getData()
    # is already largely tested by test_index(), the test matrix here can be much more
    # modest in size.  My choices is test cases are largely arbitrary just meant to show
    # that a vector of tuples will be successfully retrieved for a few types of data channels
    # and components.

    tests = {
             # dict key: (xtv_file_key, channel)          # Dictionary template
             'tv_pipe01': ('xtv1','vln-12A01'),           # edge variable in a 1D component
             'tv_pipe02': ('xtv1','cmass-12'),            # scalar variable in a 1D component
             'tv_tee01': ('xtv1','cl-10A05'),             # ghost cell of a TEE
             'tv_vessel01': ('xtv1','cixr-26A04R02T03'),  # edge variable internal to vessel
             'tv_vessel02': ('xtv1','tcolmf-26'),         # scalar variable in a VESSEL
             'tv_power01': ('xtv1','fuelAvgT-174'),       # scalar variable
             'tv_power02': ('xtv1','pctAvg-6002'),        # scalar variable
             'tv_htstr01': ('xtv1','tClad-140A03'),       # coarse mesh var at internal coarse node row
             'tv_htstr02': ('xtv1','tsurfo-140A03'),      # perm fine mesh var
             'tv_htstr03': ('xtv1','avgFuelT-140'),       # scalar
             'tv_htstrc01': ('xtv1','tchfo-140A15'),      # 1D dynamic fine mesh var internal node row
             'tv_htstrc02': ('xtv1','tchfo-140A100'),     # 1D dynamic fine mesh var at last open storage location
             'tv_htstrc03': ('xtv1','rftn-140A01R01'),    # 2D dynamic fine mesh var at bottom node row, inner node
             'tv_htstrc04': ('xtv1','rftn-140A08R05'),    # 2D dynamic fine mesh var at middle node row, internal node
             'tv_htstrc05': ('xtv1','rftn-140A17R08'),    # 2D dynamic fine mesh var at top node row, outer node
             'tv_general01': ('xtv1','massE'),            # general variable, no component association
             'tv_control01': ('xtv1','cb99003'),          # control block
             'tv_control02': ('xtv1','sv99004'),          # signal variable
             'tv_control03': ('xtv1','tp11'),             # trip

            }

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")

        (xtv_key, channel) = tests[test]

        if xtv_key in xtvOpenFiles:
            pass
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)

        xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")


        try:
            myValues[test] = xtvFile.getTimeVector(channel)
        except XTVError as e:
            writeErrorMessage('getTimeVector', channel, 0.0, errmsg=e)
            myValues[test] = None

        try:
            expectedValues[test] = pypost_getTimeVector(test, xtvFiles[xtv_key], channel)
        except subprocess.CalledProcessError as e:
            writeErrorMessage('pyPost', channel, 0.0, errmsg=e)
            expectedValues[test] = None

        checkVector(test, myValues[test], expectedValues[test])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def test_timeVectorAxial(tests_to_run):
    """Driver routine used to test the getTimeVectorAxial() function.  Results cannot
       be compared against pypost because pypost contains no similar routine.  The best
       we can do is to set the zLoc to the same height as an indexed location and compare
       against that"""

    tests = {
             # dict key: (xtv_file_key, channel, z location)                       # Dictionary template
             'tva_pipe01': ('xtv1','vln-12A03',5.5),           # edge variable in a 1D component
             'tva_tee01': ('xtv1','cl-10A02',3.61995),             # TEE variable
             'tva_vessel01': ('xtv1','cixr-26A04R02T03',4.1891),  # edge variable internal to vessel
             'tva_htstr01': ('xtv1','tClad-140A02',0.6374025),       # coarse mesh var at internal coarse node row
             'tva_htstr02': ('xtv1','tsurfo-140A06',1.09269),      # perm fine mesh var
             'tva_htstrc01': ('xtv1','tchfo-140A12',2.54971),      # 1D dynamic fine mesh var internal node row
             'tva_htstrc02': ('xtv1','rftn-140A01R01',0.0),    # 2D dynamic fine mesh var at bottom node row, inner node
             'tva_htstrc03': ('xtv1','rftn-140A12R05',2.54971),    # 2D dynamic fine mesh var at middle node row, internal node
             'tva_htstrc04': ('xtv1','rftn-140A17R08',3.6424),    # 2D dynamic fine mesh var at top node row, outer node
            }

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")

        (xtv_key, channel, zLoc) = tests[test]

        if xtv_key in xtvOpenFiles:
            pass
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)

        xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")


        try:
            myValues[test] = xtvFile.getTimeVectorAxial(channel, zLoc)
        except XTVError as e:
            writeErrorMessage('getTimeVectorAxial', channel, 0.0, zLoc, errmsg=e)
            myValues[test] = None

        try:
            expectedValues[test] = pypost_getTimeVector(test, xtvFiles[xtv_key], channel)
        except subprocess.CalledProcessError as e:
            writeErrorMessage('pyPost', channel, 0.0, errmsg=e)
            expectedValues[test] = None

        checkVector(test, myValues[test], expectedValues[test])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def test_axialVector(tests_to_run):

    tests = {
             # dict key: (xtv_file_key, channel, time)        # Dictionary template

             # This series of tests makes data requests at both an arbitrary time and an exact
             # time point.  The arbitrary time requests expose bug in pypost causing them to
             # fail.  For that reason, we added the exact time point requests (which succeed, as expected)
             'av_pipe01': ('xtv1','vln-12A01',5.0),                  # edge variable in a 1D component
             'av_pipe02': ('xtv1','vln-12A01',6.457953),             # edge variable in a 1D component

             'av_tee01': ('xtv1','cl-10A01',5.0),                    # TEE variable
             'av_tee02': ('xtv1','cl-10A01',6.457953),               # TEE variable

             'av_vessel01': ('xtv1','cixr-26A01R02T03',5.0),         # edge variable internal to vessel
             'av_vessel02': ('xtv1','roln-26A01R01T03',5.0),         # cell variable internal to vessel
             'av_vessel03': ('xtv1','cixr-26A01R02T03',6.457953),    # edge variable internal to vessel
             'av_vessel04': ('xtv1','roln-26A01R01T03',6.457953),    # cell variable internal to vessel

             'av_htstr01': ('xtv1','tClad-140A01',5.0),              # coarse mesh var at internal coarse node row
             'av_htstr02': ('xtv1','tsurfo-140A01',5.0),             # perm fine mesh var
             'av_htstr03': ('xtv1','tClad-140A01',6.457953),         # coarse mesh var at internal coarse node row
             'av_htstr04': ('xtv1','tsurfo-140A01',6.457953),        # perm fine mesh var

             'av_htstrc01': ('xtv1','tchfo-140A01',5.0),             # 1D dynamic fine mesh var internal node row
             'av_htstrc02': ('xtv1','rftn-140A01R01',5.0),           # 2D dynamic fine mesh var at bottom node row, inner node
             'av_htstrc03': ('xtv1','rftn-140A01R05',5.0),           # 2D dynamic fine mesh var at middle node row, internal node
             'av_htstrc04': ('xtv1','rftn-140A01R08',5.0),           # 2D dynamic fine mesh var at top node row, outer
             'av_htstrc05': ('xtv1','tchfo-140A01',6.457953),        # 1D dynamic fine mesh var internal node row
             'av_htstrc06': ('xtv1','rftn-140A01R01',6.457953),      # 2D dynamic fine mesh var at bottom node row, inner node
             'av_htstrc07': ('xtv1','rftn-140A01R05',6.457953),      # 2D dynamic fine mesh var at middle node row, internal node
             'av_htstrc08': ('xtv1','rftn-140A01R08',6.457953),      # 2D dynamic fine mesh var at top node row, outer

            }

    myValues = {}
    expectedValues = {}
    xtvOpenFiles = {}

    for test in sorted(tests.keys()):

        # Check the current test against the list of tests the user wants to run (from the command line).
        # If it matches, proceed.  Otherwise, iterate to the next test.
        if 'All' not in tests_to_run and test not in tests_to_run:
            continue

        # If the --prefix option was used, only run tests in this test suite that start with that prefix
        if args.prefix and not test.startswith(args.prefix):
            continue

        print
        print("--------------------------------------------------------------------------------------------------")
        print("Executing test: " + test + "....")

        (xtv_key, channel, time) = tests[test]

        if xtv_key in xtvOpenFiles:
            pass
        else:
            xtvFileHandle = open(xtvFiles[xtv_key], 'rb')
            xtvOpenFiles[xtv_key] = XtvFile(xtvFileHandle, verbose=False)

        xtvFile = xtvOpenFiles[xtv_key]  # reference the object for the current open file

        if xtvFile.times != expected_times[xtv_key]:  # Check all times in the file to see if they are what we think they are
            print("Error - The dump times have changed since the tests were initially configured.")
            print("        Some tests may not function as designed.")


        try:
            myValues[test] = xtvFile.getAxialVector(time, channel)
        except XTVError as e:
            writeErrorMessage('getAxialVector', channel, time, errmsg=e)
            myValues[test] = None

        try:
            expectedValues[test] = pypost_getAxialVector(test, xtvFiles[xtv_key], channel, time)
        except subprocess.CalledProcessError as e:
            writeErrorMessage('pyPost', channel, time, errmsg=e)
            expectedValues[test] = None

        checkVector(test, myValues[test], expectedValues[test])

    # Close all open files
    for k in xtvOpenFiles.keys():
        fObj = xtvOpenFiles[k]
        fObj.xtvFile.close()

    return


def checkResult(test, m, p):
    """Compares the result of two values to see if they are the same.  Generate a message to
       the user to indicate success or failure"""

    if args.verbose:
        print
        print("my value = " + str(m))
        print("pypost value = " + str(p))
        print

    if m is None or p is None:
        print("**"+ test + " failed**")
        return

    diff = float(m) - float(p)
    diff = round(diff,4)
    if diff != 0:
        print("**"+ test + " failed**")
    else:
        print("**"+ test + " succeeded**")


def checkErrMsg(test, m, p):
    """Compares the error message generated to what is expected.  Generate a message to
       the user to indicate success or failure"""

    if args.verbose:
        print
        print("Expected error msg : ")
        print( str(p))
        print

    if m is None or p is None:
        print("**"+ test + " failed**")
        return

    if str(m).strip() != str(p).strip():
        print("**"+ test + " failed**")
    else:
        print("**"+ test + " succeeded**")


def checkVector(test, m, p):
    """Compares the results of two different XTV requests to see if they are the same.  Generate a message to
       the user to indicate success or failure"""

    if args.verbose:
        print
        print("my value =     " + str(m))
        print
        print("pypost value = " + str(p))
        print

    if m is None or p is None:
        print("**"+ test + " failed**")
        return

    p = ast.literal_eval(p)   # The pypost result is a python list represented in string
                              # form.  Convert it back into a real python list

    for mtuple, ptuple in zip(m,p):   # now loop over each tuple in the list and compare each constituent value
        for mVal, pVal in zip(mtuple, ptuple):
            if args.verbose: print( 'Comparing ' + str(mVal) + ' to '  + str(pVal))

            # Due to differences in precision between pypost and this script, compare
            # the difference in values.  For single precision values, the result should be
            # zero to within about 4 decimals
            diff = float(mVal) - float(pVal)
            diff = round(diff,4)
            if diff != 0:
                print("**"+ test + " failed**")
                if args.verbose: print( str(mVal) + ' != '  + str(pVal))
                return
    print("**"+ test + " succeeded**")


def pypost_getZValue(test, xtv_file, channel, time, zLoc):
    pyPostInput = r"./" + str(test) + ".ppscript"
    with open(pyPostInput, 'w') as ppScript:
        ppScript.write('TRACE.openPlotFile("'+ xtv_file +'")\n')
        ppScript.write('vector = TRACE.getAxialData(0, "'+ channel + '",' + str(time) +', 0.0)\n')
        ppScript.write('dataPoint = vector.yvalAt(' + str(zLoc) + ')\n')
        ppScript.write('print dataPoint\n')
        ppScript.write('TRACE.closeAll()\n')

    with open(os.devnull, 'w') as devnull:  # open up /dev/null so we can redirect stderr to it
        #value = subprocess.check_output([pypost, pyPostInput], stderr=devnull, encoding='utf-8')
        value = subprocess.check_output([pypost, pyPostInput], stderr=devnull).decode('utf-8')
    return float(value.strip())


def pypost_getSingleValue(test, xtv_file, channel, time):
    pyPostInput = r"./" + str(test) + ".ppscript"
    with open(pyPostInput, 'w') as ppScript:
        ppScript.write('TRACE.openPlotFile("'+ xtv_file +'")\n')
        ppScript.write('vector = TRACE.getData(0, "'+ channel + '")\n')
        ppScript.write('dataPoint = vector.yvalAt(' + str(time) + ')\n')
        ppScript.write('print dataPoint\n')
        ppScript.write('TRACE.closeAll()\n')

    with open(os.devnull, 'w') as devnull:  # open up /dev/null so we can redirect stderr to it
        #value = subprocess.check_output([pypost, pyPostInput], stderr=devnull, encoding='utf-8')
        value = subprocess.check_output([pypost, pyPostInput], stderr=devnull).decode('utf-8')
    return float(value.strip())


def pypost_getTimeVector(test, xtv_file, channel):
    pyPostInput = r"./" + str(test) + ".ppscript"
    with open(pyPostInput, 'w') as ppScript:
        ppScript.write('TRACE.openPlotFile("'+ xtv_file +'")\n')
        ppScript.write('vector = TRACE.getData(0, "'+ channel + '")\n')
        ppScript.write('print vector\n')
        ppScript.write('TRACE.closeAll()\n')

    with open(os.devnull, 'w') as devnull:  # open up /dev/null so we can redirect stderr to it
        #value = subprocess.check_output([pypost, pyPostInput], stderr=devnull, encoding='utf-8')
        value = subprocess.check_output([pypost, pyPostInput], stderr=devnull).decode('utf-8')
    return value


def pypost_getAxialVector(test, xtv_file, channel, time):
    pyPostInput = r"./" + str(test) + ".ppscript"
    with open(pyPostInput, 'w') as ppScript:
        ppScript.write('TRACE.openPlotFile("'+ xtv_file +'")\n')
        ppScript.write('vector = TRACE.getAxialData(0, "'+ channel + '", ' + str(time) + ')\n')
        ppScript.write('print vector\n')
        ppScript.write('TRACE.closeAll()\n')

    with open(os.devnull, 'w') as devnull:  # open up /dev/null so we can redirect stderr to it
        #value = subprocess.check_output([pypost, pyPostInput], stderr=devnull, encoding='utf-8')
        value = subprocess.check_output([pypost, pyPostInput], stderr=devnull).decode('utf-8')
    return value


def writeErrorMessage(routine, channel, time, zLoc=None, errmsg=None):
    """Standardizes the output when generating error messages"""

    if not args.verbose:
        return

    if routine == 'XtvFile':
       print
       print("**************************************************************************************************")
       string = "Error reading XTV file header"
       print( string)
       if errmsg: print( errmsg)
       print("**************************************************************************************************")
    else:

       print
       print("**************************************************************************************************")
       string = "Could not retrieve value for " + channel + " @ time = " + str(time)
       if zLoc is not None:
           string = string + " @ z = " + str(zLoc)
       print("In call to " + routine)
       print( string)
       if errmsg: print( errmsg)
       print("**************************************************************************************************")

    print( traceback.format_exc())


def getArguments():
    """ Sets up the command line argument parser and returns the parse_args
    object """

    usage=textwrap.dedent("""
    **DESCRIPTION**
      The xtvreader.py script is both a Python library that you may include
      in your own python-based plotting tools, and a stand-alone script that
      may be used to query and retrieve data channel values from an XTV file.

      A complete description of the API and internal methods that may be used
      in your own python scripts that import this library can be found in the
      HTML-based documentation for the TRACE test suite.

      If you intend to use this script as a standalone tool for accessing XTV
      information, the included functionality is documented here.

    **RETRIEVING XTV DATA**

      To extract a data channel from the XTV file, simply run this script and
      supply the XTV file name and data channel ID using the -i (--input) and
      -c (--channel) command line arguments.  For example, to get the void
      fraction for PIPE 10, you would type the following:

         > ./xtvreader.py -i <file.xtv> -c alpn-10A01

      Replace <file.xtv> with the name of your specific XTV file.

      You may also provide a list of data channels if you wish to get data
      for more than one data channel at a time.

         > ./xtvreader.py -i <file.xtv> -c pn-10A01,pn-10A02

      By default, data will be printed to the screen in two columns denoting
      the time and the values of interest.  Line 1 will regurgitate the name 
      of the data channel. Lines 2 and 3 provide column labels and units for 
      each columne, respectively.

      An option also exists to output the data as a vector of tuples that 
      denote each (time, value) pair.  See the description of the --output
      command line option below.

      Extracting data appropriate for creating an axial plot is fairly easy
      using the --axial and --at command line options.  In this mode, you
      provide a list of one or more data channel ID's, as shown above, and
      for each of the referenced components, the axial axial index gets
      ignored and instead, the script retrieves data pairs for all of
      the axial locations in that component at a particular time point.

      So let's say we wish to generate an axial plot of the clad surface
      temperatures of HTSTR 1001 at the last time point in the model.  We
      would use the following command:

         > ./xtvreader.py -i <file.xtv> -c tsurfo-1001A01 --axial

      To specify a list of different time points, we would add the --at
      command line option, like this:

         > ./xtvreader.py -i <file.xtv> -c tsurfo-1001A01 --axial --at 1.0,2.0,-1.0

      The use of a negative value denotes the very last time point in
      the XTV file.


    **QUERYING THE XTV FILE**

      To query the XTV file to see if a particular data channel is
      contained in the XTV file, you can type the following:

         > ./xtvreader.py -i file.xtv --query pipe-A01

      The script will print a simple message denoting whether that data
      channel happens to exist in the file.

      Of course, unless you are extremely familiar with TRACE and the input
      model you are working with, you may not know, a priori, which specific
      data channels may or may not exist within an XTV file.  The --show
      command line option to display the meta data of the XTV file.

      To print out a list of all the data channels in the XTV file:

         > ./xtvreader.py -i file.xtv --show=all

      Of course, depending on the size of your input model, the list the above
      command displays to the screen may be huge.  To alleviate this problem,
      a truncated list of data channels may generated using the --show=basic
      option.  In that case, the script will show the data channels for every
      component, but only for the highest mesh index in each direction.
         
         > ./xtvreader.py -i file.xtv --show=basic

      If you wish to further limit the data channels displayed to a single
      component, you can use the --show="id" option.  In that instance, you
      will see all the data channels for a particular component, limited to 
      just the maximum mesh index as with the "basic" option described above.

         > ./xtvreader.py -i file.xtv --show=id --id=101

      If you wish to see all the data channel ID's for a particular component
      and specific XTV variable name, use the --show="var" option, as follows:

         > ./xtvreader.py -i file.xtv --show=var --var=alpn --id=101

      To get just a list of all the components and their ID numbers, use the
      --show="comps" command line option:

         > ./xtvreader.py -i file.xtv --show=comps

      Finally, you can also retrieve the list of time points contained in the
      XTV file using the --show=times option.

         > ./xtvreader.py -i file.xtv --show=times

        
    **EXERCISING THE BUILT-IN UNIT TESTS**

      The xtvreader.py script includes a built-in test suite to verify that the
      various worker functions in the XTVFile class can accurately retrieve the
      correct values from the XTV file.

      Running all the tests is easy.  Just invoke xtvreader.py from the command
      line, like this::

          > ./xtvreader.py --unit_test
               or
          > ./xtvreader.py -u

      Each individual test represents a singular attempt to place a request for
      XTV data using one of the functions exposed by this module.  In most
      instances, the result of this data pull request is then compared to a
      similar request made using the PyPost tool that comes with AptPlot.  If the
      result is the same (or within a reasonable epsilon for tests that involve
      interpolations), the test is reported as a success.  If not, the test is
      reported as having failed.  It is worth noting that while comparisons to
      PyPost values don't technically represent conclusive proof that the
      routines are working correctly (because both tools could be wrong in the
      same way), the chances of this happening are slim.

      A sequence of tests are also performed to show/prove that the right error
      messages are generated in the right context.  In these instances, rather
      than compare retrieved results to pypost, the tests are made to call the
      functions in this module with intentionally bad data in the hope that it
      will generate an error message.  Any error message so produced is then
      compared against the the expected error message for that situation.  If the
      error messages are the same, then the test is considered a success.

      By default, the test routines limit the amount of output to just report on
      the test being performed and the ultimate result.  If you wish to see more
      information about the test, like what exact values have been compared, etc,
      then you can use the --verbose option.

      Internally, the individual tests are captured in python dictionaries that
      define the information needed to place the necessary calls to a particular
      function of interest.  Adding new tests is as simple as locating the
      appropriate dictionary in the appropriate "test_xxx()" routine and adding
      the new entry with an appropriate ID string as the dict key.

      Each individual test has a unique identifier, but tests within a given
      sequence share a common prefix.  The prefixes used are as follows:

         * ``a_``   = indicates tests meant to exercise the getAxialDataChannel() function
 
         * ``av_``  = indicates tests meant to exercise the getAxialVector() function

         * ``i_``   = indicates tests meant to exercise the getDataChannel() function

         * ``tv_``  = indicates tests meant to exercise the getTimeVector() function

         * ``tva_`` = indicates tests meant to exercise the getTimeVectorAxial function

         * ``err``  = indicates tests meant to exercise the error handling logic (the lack
                    of an underscore (_) is intentional)

      We recognize that it would sometimes be advantageous to only run a single
      test or subset of tests rather than wait for the entire test set to
      complete.  For this reason, some additional command line options have been
      added.  For example, if you just want to execute a single test, then you
      would execute the script as follows::

          > ./xtvreader.py -u --test a_htstr01

      This command line option also takes a comma-separate list of tests, like
      this::

          > ./xtvreader.py -u --test a_htstr01,a_pipe01,i_vessel13
 
      For tests whose ID strings only differ by a numeric value, it is also
      possible to define a range of test ID's using the --range option, like
      this::

          > ./xtvreader.py -u --range a_htstr01-a_htstr10

      In that case, the script treats the two values as bounding values and
      generates a list of ID strings to test, filling in the missing integers as
      it goes.

      Finally, it is also possible to limit the tests executed to only a
      particular sequence that share the same prefix.  In that case, use the
      --prefix option, like this::

          > ./xtvreader.py -u --prefix tv_


      By default, the script will look for PyPost in either of the two
      standard installation directories on a Windows system, namely
      ``c:\Program Files\AptPlot\\bin`` or ``%USERPROFILE%\AptPlot\\bin``.  But
      sometimes, it may be located in a different location.  To ensure that PyPost
      is properly executed, you can use the --pypost command line option to
      redefine its actual location on your particular filesystem.

    """)
    parser = argparse.ArgumentParser(description=usage,
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-a", "--axial", action="store_true", default=False,
                        help=textwrap.dedent('''
                        Extract axial plot data.  When this option is set, the script will
                        retrieve data pairs that correspond to the axial location and parameter
                        value of interest for a given component and for one or more time points 
                        (see the --at option below).  If no specific time point is specified, 
                        then the last time point is used.\n
                        '''))
    parser.add_argument("--at", type=lambda s: [str(item) for item in s.split(',')],
                        default=None, metavar='TIMES',
                        help=textwrap.dedent('''
                        Time(s) at which to extract axial data.  TIMES denotes a list
                        of time points for which axial data will be retrieved if --axial is
                        set.  By default, the last time point in the XTV file is used if this
                        option is not specified and --axial is true.\n
                        '''))
    parser.add_argument("-c", "--channel", type=lambda s: [str(item) for item in s.split(',')],
                        default=None, metavar="XTV_CHANNELS",
                        help=textwrap.dedent('''
                        Data channel name(s) you wish to extract.  XTV_CHANNELS denotes one
                        or more XTV data channel names, separated by commas, which have the form
                        <var name>-<comp num>A<mesh location>R<mesh location>T<mesh location>.
                        <mesh location> is typically a two digit integer prepended with a
                        zero when the value is in the range of 1-9.
                        For example, to get the void fraction from cell 3 of PIPE 100, you
                        would specify "alpn-100A03".\n
                        '''))
    parser.add_argument("--id", type=str, default=None, metavar='COMP_NUM',
                        help=textwrap.dedent('''
                        Component number.  Only needed when --show="var" or --show="id"\n
                        '''))
    parser.add_argument("-i", "--input", type=str,
                        default=None,
                        help=textwrap.dedent('''
                        XTV file you wish to pull a variable from.\n
                        '''))
    parser.add_argument("-o", "--output", type=str, default="cols", choices=['cols', 'vec'],
                        help=textwrap.dedent('''
                        This option denotes the form of output that XTV data will be written
                        to the standard output (stdout).  A value of "cols" indicates the
                        data will be written in column format.  A value of "vec" indicates
                        the data will be written out as a vector of comma-delimited (time, 
                        value) pairs.
                        '''))
    parser.add_argument("--prefix", type=str,
                        default=None,
                        help=textwrap.dedent('''
                        Prefix string of the test series that you wish to run.  This option
                        only has an effect when --unit_test is used.\n
                        '''))
    parser.add_argument("--pypost", type=str,
                        default=None,
                        help=textwrap.dedent('''
                        Path to the PyPost executable.  This option only has an effect when
                        --unit_test is used.\n
                        '''))
    parser.add_argument("-q", "--query", type=str, default=None, metavar="XTV_CHANNEL",
                        help=textwrap.dedent('''
                        Query whether a particular data channel exists in the XTV file.  XTV_CHANNEL
                        should have the same format as required by the --channel command line argument
                        described above.\n
                        '''))
    parser.add_argument("--range", type=lambda s: [str(item) for item in s.split('-')],
                        default=None,
                        help=textwrap.dedent('''
                        Define a range of tests to execute by providing a pair of test ID's
                        separated by a dash.  The script will attempt to fill in the missing
                        integers.  This option only has an effect when --unit_test is used.\n
                        '''))
    parser.add_argument("-s", "--show", type=str, default="basic", choices=['all', 'basic', 'comps', 'id', 'var', "times"],
                        help=textwrap.dedent('''
                        Show a list of available data channels in the XTV file.  This optioo offers
                        several values that govern the specific behavior required.

                        "all"   : printout the full list of every data channel name contained in the XTV file

                        "basic" : printout a truncated list of data channel names in which the mesh index shown
                                  corresponds to the maximum index for that data channel.  So if the value
                                  "alpn-1A05" is displayed, then it can be assumed that the data channels for cells 1-4 are
                                  also in the graphics file.

                        "var"   : printout the list of data channels for a specific XTV variable in a specific component.
                                  This option requires the --id and --var command line options.

                        "id"    : printout the list of data channels for a specific component number.  This option
                                  requires the --id and --var command line options.

                        "comps" : printout a list of all the component identifiers in the XTV file\n

                        "times" : printout a list of the individual time points in the XTV file\n
                        '''))
    parser.add_argument("--tests", type=lambda s: [str(item) for item in s.split(',')],
                        default='All',
                        help=textwrap.dedent('''
                        Comma-separated list of test names to execute. This option only has an
                        effect when --unit_test is used.  Default is All.\n
                        '''))
    parser.add_argument("--unit_test", action="store_true", default=False, dest="unit_test",
                        help=textwrap.dedent('''
                        Execute the built-in unit test framework.\n
                        '''))
    parser.add_argument("--var", type=str, default=None, metavar='XTV_VAR',
                        help=textwrap.dedent('''
                        XTV variable name.  Only needed when --show="var"\n
                        '''))
    parser.add_argument("-v", "--verbose", action="store_true", default=False, dest="verbose",
                        help=textwrap.dedent('''
                        Generate more verbose output regarding test results for each test.\n
                        '''))
    return parser


def string_range(start, stop, step=1, prefix='', suffix=''):
    """Construct a range of strings"""

    r = range(start, stop, step)
    return [str(prefix)+str(x).zfill(2)+str(suffix) for x in r]


if __name__ == '__main__':
    global args
    global pypost

    # Process command line arguments
    parser = getArguments()
    args = parser.parse_args()

    if args.input:
       # The user supplied an XTV file from the command line.  The assumption is we want to do
       # something with it, so open it up, parse it, and instantiate an object we can work with.
       xtvFileHandle = open(args.input, 'rb')
       xtvFile = XtvFile(xtvFileHandle, verbose=False)

       if args.channel:  # User provided a data channel name via command line.  Extract its data.
          for chan in args.channel:

             if args.axial:  # Axial data processing
                if args.at:
                   times = [float(x) for x in args.at]
                else:
                   times = [xtvFile.times[-1]]
                for time in times:
                   if time < 0.0:
                      time = xtvFile.times[-1] 
                   array = xtvFile.getAxialVector(time, chan)
                   print ()
                   print ("Data channel : " + chan)
                   print ("At time = " + str(time) + " secs")
                   if args.output == 'vec':
                      print ("Description  : (axial location, " + xtvFile.getDescription(chan).rstrip()+ ")")
                      print ("Units        : (m, " + xtvFile.getUnits(chan).rstrip() + ")")
                      print (array)
                   else:
                      print ("axial location".ljust(21) + xtvFile.getDescription(chan).rstrip())
                      print ("meters              " + xtvFile.getUnits(chan).rstrip())
                      for x,y in array:
                         print (str(x).ljust(20,' '), y)

             else:  # Time history data processing
                array = []
                array = xtvFile.getTimeVector(chan)
                print ()
                print ("Data channel : " + chan)
                if args.output == 'vec':
                   print ("Description  : (time, " + xtvFile.getDescription(chan).rstrip()+ ")")
                   print ("Units        : (seconds, " + xtvFile.getUnits(chan).rstrip() + ")")
                   print (array)
                else:
                   print ("time".ljust(21) + xtvFile.getDescription(chan).rstrip())
                   print ("seconds             " + xtvFile.getUnits(chan).rstrip())
                   for x,y in array:
                      print (str(x).ljust(20,' '), y)



       elif args.query:  # User simply wishes to query whether a particular data channel exists in the XTV file.
          xtvChannelDict = xtvFile.getList(list_all=True, with_desc=False)

          found = False
          for comp in xtvChannelDict.keys():
             for xtvChan in xtvChannelDict[comp]:
                if args.query == str(xtvChan):
                   found = True
                   break
             if found:
                break
          if found:
             print (" ** Found the data channel **")
          else:
             print (" ** Data channel not found! **")
      
       elif args.show:  # User wants a list of available data channels.  Several modes exist.
                        #   1) print out the entire list of data channels to the screen (--show=all)
                        #   2) print out an abbreviated list of data channels to the screen  (--show=basic)
                        #   3) print out a list of all the components in the XTV file (--show=comps)
                        #   4) print out an abbreviated list of data channels for a particular component (--show=id --id=NUM)
                        #   5) print out the specific data channels for a particular XTV variable 
                        #      in a specific component (--show=var --var=VAR --id=NUM)
          if args.show == 'all':
             xtvChannelDict = xtvFile.getList(list_all=True, with_desc=True)
             for comp in xtvChannelDict.keys():
                print (comp)
                for xtvChan, desc in xtvChannelDict[comp]:
                   print ("    " + str(xtvChan).ljust(24," ") + "  :  " + desc)
          elif args.show == 'basic': 
             xtvChannelDict = xtvFile.getList(list_all=False, with_desc=True)
             for comp in xtvChannelDict.keys():
                print (comp)
                for xtvChan, desc in xtvChannelDict[comp]:
                   print ("    " + str(xtvChan).ljust(24," ") + "  :  " + desc)
          elif args.show == 'var':
             xtvChannelDict = xtvFile.getList(list_all=True, with_desc=False)
             for comp_id in xtvChannelDict.keys():
                if comp_id.endswith("-"+args.id):
                   chanList = xtvChannelDict[comp_id]
                   for chan in chanList:
                      if chan.startswith(args.var):
                         print (chan)
          elif args.show == 'id':
             xtvChannelDict = xtvFile.getList(list_all=False, with_desc=True)
             for comp_id in xtvChannelDict.keys():
                if comp_id.endswith("-"+args.id):
                   for (xtvChan, desc) in xtvChannelDict[comp_id]:
                      print ("    " + str(xtvChan).ljust(24," ") + "  :  " + desc)
          elif args.show == 'comps':
             xtvChannelDict = xtvFile.getList(list_all=False, with_desc=False)
             for comp in xtvChannelDict.keys():
                print (comp)
          elif args.show == 'times':
             if args.output == "vec":
                print (xtvFile.times)
             else:
                for x in xtvFile.times:
                   print (x)

       else:
          # User provided an XTV file, but we don't know what do with it.  Do nothing.
          pass

    elif args.unit_test:
       # User may have used command line args to pare back the tests that get run.  Make sense of that now
       if args.range:                 # User defined a range of tests to run.  Turn that range into a list
           start_test = args.range[0]
           end_test = args.range[1]
   
           #
           # test names look like this:  xxxx01, xxxx02, xxxx03......
           # need to parse these strings and separate the numbers from the text
           #
           # Extract the digits and base name from the left bounding string and convert to an integer
           regex = re.compile(r'(?P<base>\w+)(?P<num>\d\d+)')
           m = regex.match(start_test)
           if not m:
               print("Error - invalid string used in the --range cmd line switch")
               exit()
           start_num = int(m.group('num'))
           start_base = m.group('base')
   
           # Extract the digits and base name from the right bounding string and convert to an integer
           m = regex.match(end_test)
           if not m:
               print("Error - invalid string used in the --range cmd line switch")
               exit()
           end_num = int(m.group('num'))
           end_base = m.group('base')
   
           if start_base != end_base:
               print("Error - the test names in the --range argument do not have the same root name")
               exit()
   
           tests_to_run = string_range(start_num, end_num+1, 1, start_base)  # turn the numbers into a range of values and re-construct into test names
       else:
           tests_to_run = args.tests
   
       if args.pypost:      # User has defined the location of PyPost from the command line.  Use it, no questions asked.
           pypost = args.pypost.strip()
           if sys.platform == 'cygwin':
               #pypost = subprocess.check_output(['cygpath', '-u', pypost], encoding='utf-8').rstrip('\n')
               pypost = subprocess.check_output(['cygpath', '-u', pypost]).decode('utf-8').rstrip('\n')
       else:
           # Look for PyPost in the usual places.  If it isn't under Program Files, then look for it
           # in the user's profile location.  If it isn't there either, just quit
           pp_default = 'c:/Program Files/AptPlot/bin/pypost.exe'
           if sys.platform == 'cygwin':    # transform Windows path to unix paths when cygwin is used
               #pp_default = subprocess.check_output(['cygpath', '-u', pp_default], encoding='utf-8').rstrip('\n')
               pp_default = subprocess.check_output(['cygpath', '-u', pp_default]).decode('utf-8').rstrip('\n')
   
           if os.path.isfile(pp_default):   # Is our assumption correct?  Is PyPost in Program Files?
               pypost = pp_default
           else:
               user_profile = os.getenv('USERPROFILE')
               pp_default = os.path.join(user_profile, 'AptPlot', 'bin', 'pypost.exe')
               if sys.platform == 'cygwin':              # transform Windows path to unix paths when cygwin is used
                   #pp_default = subprocess.check_output(['cygpath', '-u', pp_default], encoding='utf-8').rstrip('\n')
                   pp_default = subprocess.check_output(['cygpath', '-u', pp_default]).decode('utf-8').rstrip('\n')
               if os.path.isfile(pp_default):
                   pypost = pp_default
               else:
                   print("Can't locate PyPost.  Use the --pypost option to define it's location yourself")
                   exit()
   
   
       tests(tests_to_run)
