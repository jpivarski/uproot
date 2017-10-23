#!/usr/bin/env python

# Copyright (c) 2017, DIANA-HEP
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
# 
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
# 
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import struct
import re
from collections import namedtuple

import numpy

import uproot.const
from uproot.source.compressed import Compression
from uproot.source.compressed import CompressedSource
from uproot.source.cursor import Cursor

################################################################ ROOTDirectory

class ROOTDirectory(object):
    """Represents a ROOT file or subdirectory; use to extract objects.

    Do not create it directly; use uproot.open or uproot.iterator to open files.

        * `dir[name]` or `dir.get(name, cycle=None)` to extract an object (aware of '/' and ';' notations).
        * `dir.name` is the name of the directory or file (as written in the file itself).
        * `dir.compression` (with `algo`, `level` and `algoname` attributes) describes the compression.

    The following have arguments `recursive=False, filtername=lambda name: True, filterclass=lambda name: True`:

        * `dir.keys()` iterates over key names (bytes objects) in this directory.
        * `dir.values()` iterates over the contents (ROOT objects) in this directory.
        * `dir.items()` iterates over key-value pairs in this directory.
        * `dir.classes()` iterates over key-classname pairs (both bytes objects) in this directory.

    Filters allow you to exclude names (bytes objects) or class names (bytes objects) from the search.
    Eliminating a directory does not eliminate its contents.

    The following are shortcuts for recursive=True:

        * `dir.allkeys()`
        * `dir.allvalues()` 
        * `dir.allitems()` 
        * `dir.allclasses()` 
    """

    class _FileContext(object):
        def __init__(self, streamerinfos, classes, compression):
            self.streamerinfos, self.classes, self.compression = streamerinfos, classes, compression

    @staticmethod
    def read(source, *args):
        if len(args) == 0:
            # See https://root.cern/doc/master/classTFile.html
            cursor = Cursor(0)
            magic, fVersion = cursor.fields(source, ROOTDirectory._format1)
            if magic != b"root":
                raise ValueError("not a ROOT file (starts with {0} instead of 'root')".format(repr(magic)))
            if fVersion < 1000000:
                fBEGIN, fEND, fSeekFree, fNbytesFree, nfree, fNbytesName, fUnits, fCompress, fSeekInfo, fNbytesInfo, fUUID = cursor.fields(source, ROOTDirectory._format2_small)
            else:
                fBEGIN, fEND, fSeekFree, fNbytesFree, nfree, fNbytesName, fUnits, fCompress, fSeekInfo, fNbytesInfo, fUUID = cursor.fields(source, ROOTDirectory._format2_big)

            # classes requried to read streamers (bootstrap)
            streamerclasses = {b"TStreamerInfo":             TStreamerInfo,
                               b"TStreamerElement":          TStreamerElement,
                               b"TStreamerBase":             TStreamerBase,
                               b"TStreamerBasicType":        TStreamerBasicType,
                               b"TStreamerBasicPointer":     TStreamerBasicPointer,
                               b"TStreamerLoop":             TStreamerLoop,
                               b"TStreamerObject":           TStreamerObject,
                               b"TStreamerObjectPointer":    TStreamerObjectPointer,
                               b"TStreamerObjectAny":        TStreamerObjectAny,
                               b"TStreamerObjectAnyPointer": TStreamerObjectAnyPointer,
                               b"TStreamerString":           TStreamerString,
                               b"TStreamerSTL":              TStreamerSTL,
                               b"TStreamerSTLString":        TStreamerSTLString,
                               b"TStreamerArtificial":       TStreamerArtificial,
                               b"TObjArray":                 TObjArray}

            streamercontext = ROOTDirectory._FileContext(None, streamerclasses, Compression(fCompress))
            streamerkey = TKey.read(source, Cursor(fSeekInfo), streamercontext)
            streamerinfos = _readstreamers(streamerkey._source, streamerkey._cursor, streamercontext)
            classes = _defineclasses(streamerinfos)

            context = ROOTDirectory._FileContext(streamerinfos, classes, Compression(fCompress))

            keycursor = Cursor(fBEGIN)
            mykey = TKey.read(source, keycursor, context)
            return ROOTDirectory.read(source, keycursor, context, mykey)

        else:
            cursor, context, mykey = args

            # See https://root.cern/doc/master/classTDirectoryFile.html.
            fVersion, fDatimeC, fDatimeM, fNbytesKeys, fNbytesName = cursor.fields(source, ROOTDirectory._format3)
            if fVersion <= 1000:
                fSeekDir, fSeekParent, fSeekKeys = cursor.fields(source, ROOTDirectory._format4_small)
            else:
                fSeekDir, fSeekParent, fSeekKeys = cursor.fields(source, ROOTDirectory._format4_big)

            subcursor = Cursor(fSeekKeys)
            headerkey = TKey.read(source, subcursor, context)

            nkeys = subcursor.field(source, ROOTDirectory._format5)
            keys = [TKey.read(source, subcursor, context) for i in range(nkeys)]

            out = ROOTDirectory(mykey.fName, context, keys)

            # source may now close the file (and reopen it when we read again)
            source.dismiss()
            return out

    _format1       = struct.Struct("!4si")
    _format2_small = struct.Struct("!iiiiiiBiii18s")
    _format2_big   = struct.Struct("!iqqiiiBiqi18s")
    _format3       = struct.Struct("!hIIii")
    _format4_small = struct.Struct("!iii")
    _format4_big   = struct.Struct("!qqq")
    _format5       = struct.Struct("!i")

    def __init__(self, name, context, keys):
        self.name, self._context, self._keys = name, context, keys

    @property
    def compression(self):
        return self._context.compression

    def __repr__(self):
        return "<ROOTDirectory {0} at 0x{1:012x}>".format(repr(self.name), id(self))

    def __getitem__(self, name):
        return self.get(name)

    def __len__(self):
        return len(self._keys)

    def __iter__(self):
        return self.keys()

    @staticmethod
    def _withcycle(key):
        return "{0};{1}".format(key.fName.decode("ascii"), key.fCycle).encode("ascii")

    def keys(self, recursive=False, filtername=lambda name: True, filterclass=lambda classname: True):
        """Iterates over key names (bytes objects) in this directory.

            * `recursive` if `True`, descend into subdirectories; if `False` (the default), do not.
            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        for key in self._keys:
            if filtername(key.fName) and filterclass(key.fClassName):
                yield self._withcycle(key)

            if recursive and key.fClassName == b"TDirectory":
                for name in key.get().keys(recursive, filtername, filterclass):
                    yield "{0}/{1}".format(self._withcycle(key).decode("ascii"), name.decode("ascii")).encode("ascii")

    def values(self, recursive=False, filtername=lambda name: True, filterclass=lambda classname: True):
        """Iterates over the contents (ROOT objects) in this directory.

            * `recursive` if `True`, descend into subdirectories; if `False` (the default), do not.
            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        for key in self._keys:
            if filtername(key.fName) and filterclass(key.fClassName):
                yield key.get()

            if recursive and key.fClassName == b"TDirectory":
                for value in key.get().values(recursive, filtername, filterclass):
                    yield value

    def items(self, recursive=False, filtername=lambda name: True, filterclass=lambda classname: True):
        """Iterates over key-value pairs in this directory.

            * `recursive` if `True`, descend into subdirectories; if `False` (the default), do not.
            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        for key in self._keys:
            if filtername(key.fName) and filterclass(key.fClassName):
                yield self._withcycle(key), key.get()

            if recursive and key.fClassName == b"TDirectory":
                for name, value in key.get().items(recursive, filtername, filterclass):
                    yield "{0}/{1}".format(self._withcycle(key).decode("ascii"), name.decode("ascii")).encode("ascii"), value

    def classes(self, recursive=False, filtername=lambda name: True, filterclass=lambda classname: True):
        """Iterates over key-classname pairs (both bytes objects) in this directory.

            * `recursive` if `True`, descend into subdirectories; if `False` (the default), do not.
            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        for key in self._keys:
            if filtername(key.fName) and filterclass(key.fClassName):
                yield self._withcycle(key), key.fClassName

            if recursive and key.fClassName == b"TDirectory":
                for name, classname in key.get().classes(recursive, filtername, filterclass):
                    yield "{0}/{1}".format(self._withcycle(key).decode("ascii"), name.decode("ascii")).encode("ascii"), classname

    def allkeys(self, filtername=lambda name: True, filterclass=lambda classname: True):
        """Recursively iterates over key names (bytes objects) in this directory.

            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        return self.keys(True, filtername, filterclass)

    def allvalues(self, filtername=lambda name: True, filterclass=lambda classname: True):
        """Recursively iterates over the contents (ROOT objects) in this directory.

            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        return self.values(True, filtername, filterclass)

    def allitems(self, filtername=lambda name: True, filterclass=lambda classname: True):
        """Recursively iterates over key-value pairs in this directory.

            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        return self.items(True, filtername, filterclass)

    def allclasses(self, filtername=lambda name: True, filterclass=lambda classname: True):
        """Recursively iterates over key-classname pairs (both bytes objects) in this directory.

            * `filtername` must be callable; results are returned only if `filtername(key name)` returns `True`.
            * `filterclass` must be callable; results are returned only if `filterclass(class name)` returns `True`.

        When iterating recursively, eliminating a directory does not eliminate its contents.
        """
        return self.classes(True, filtername, filterclass)

    def get(self, name, cycle=None):
        """Get an object from the directory, interpreting '/' as subdirectories and ';' to delimit cycle number.

        Synonym for `dir[name]`.

        An explicit `cycle` overrides any number after ';'.
        """
        if hasattr(name, "encode"):
            name = name.encode("ascii")

        if b"/" in name:
            out = self
            for n in name.split(b"/"):
                out = out.get(n, cycle)
            return out

        else:
            if cycle is None and b";" in name:
                at = name.rindex(b";")
                name, cycle = name[:at], name[at + 1:]
                cycle = int(cycle)

            for key in self._keys:
                if key.fName == name:
                    if cycle is None or key.fCycle == cycle:
                        return key.get()
            raise KeyError("not found: {0}".format(repr(name)))

    def __enter__(self, *args, **kwds):
        return self

    def __exit__(self, *args, **kwds):
        pass

################################################################ helper functions for common tasks

def _startcheck(source, cursor):
    start = cursor.index
    cnt, vers = cursor.fields(source, _startcheck._format_cntvers)
    cnt = int(numpy.int64(cnt) & ~uproot.const.kByteCountMask)
    return start, cnt + 4, vers
_startcheck._format_cntvers = struct.Struct("!IH")

def _endcheck(start, cursor, cnt):
    observed = cursor.index - start
    if observed != cnt:
        raise ValueError("object has {0} bytes; expected {1}".format(observed, cnt))

def _skiptobj(source, cursor):
    version = cursor.field(source, _skiptobj._format1)
    if numpy.int64(version) & uproot.const.kByteCountVMask:
        cursor.skip(4)
    fUniqueID, fBits = cursor.fields(source, _skiptobj._format2)
    fBits = numpy.uint32(fBits) | uproot.const.kIsOnHeap
    if fBits & uproot.const.kIsReferenced:
        cursor.skip(2)
_skiptobj._format1 = struct.Struct("!h")
_skiptobj._format2 = struct.Struct("!II")

def _nametitle(source, cursor):
    start, cnt, vers = _startcheck(source, cursor)
    _skiptobj(source, cursor)
    name = cursor.string(source)
    title = cursor.string(source)
    _endcheck(start, cursor, cnt)
    return name, title

def _readanyref(source, cursor, context):
    beg = cursor.index - cursor.origin
    bcnt = cursor.field(source, struct.Struct("!I"))

    if numpy.int64(bcnt) & uproot.const.kByteCountMask == 0 or numpy.int64(bcnt) == uproot.const.kNewClassTag:
        vers = 0
        start = 0
        tag = bcnt
        bcnt = 0
    else:
        vers = 1
        start = cursor.index - cursor.origin
        tag = cursor.field(source, struct.Struct("!I"))

    if numpy.int64(tag) & uproot.const.kClassMask == 0:
        # reference object
        if tag == 0:
            return None                             # return null

        elif tag == 1:
            raise NotImplementedError("tag == 1 means self; not implemented yet")

        elif tag not in cursor.refs:
            # jump past this object
            cursor.index = cursor.origin + beg + bcnt + 4
            return None                             # return null

        else:
            return cursor.refs[tag]                 # return object

    elif tag == uproot.const.kNewClassTag:
        # new class and object
        cname = cursor.cstring(source)

        fct = context.classes.get(cname, Undefined)

        if vers > 0:
            cursor.refs[start + uproot.const.kMapOffset] = fct
        else:
            cursor.refs[len(cursor.refs) + 1] = fct

        obj = fct.read(source, cursor, context)

        if vers > 0:
            cursor.refs[beg + uproot.const.kMapOffset] = obj
        else:
            cursor.refs[len(cursor.refs) + 1] = obj

        return obj                                  # return object

    else:
        # reference class, new object
        ref = int(numpy.int64(tag) & ~uproot.const.kClassMask)

        if ref not in cursor.refs:
            raise IOError("invalid class-tag reference")

        fct = cursor.refs[ref]                      # reference class

        if fct not in context.classes.values():
            raise IOError("invalid class-tag reference (not a factory)")

        obj = fct.read(source, cursor, context)      # new object

        if vers > 0:
            cursor.refs[beg + uproot.const.kMapOffset] = obj
        else:
            cursor.refs[len(cursor.refs) + 1] = obj

        return obj                                  # return object

def _readstreamers(source, cursor, context):
    start, cnt, vers = _startcheck(source, cursor)

    _skiptobj(source, cursor)
    name = cursor.string(source)
    size = cursor.field(source, struct.Struct("!i"))
    _format_n = struct.Struct("!B")

    streamerinfos = []
    for i in range(size):
        streamerinfos.append(_readanyref(source, cursor, context))
        assert isinstance(streamerinfos[-1], TStreamerInfo)
        # options not used, but they must be read in
        n = cursor.field(source, _format_n)
        cursor.bytes(source, n)

    _endcheck(start, cursor, cnt)
    return streamerinfos

def _defineclasses(streamerinfos):
    classes = {b"TObject":                   TObject,
               b"TNamed":                    TNamed,
               b"TString":                   TString,
               b"TObjArray":                 TObjArray,
               b"TList":                     TList,
               b"TArrayC":                   TArrayC,
               b"TArrayS":                   TArrayS,
               b"TArrayI":                   TArrayI,
               b"TArrayL":                   TArrayL,
               b"TArrayL64":                 TArrayL64,
               b"TArrayF":                   TArrayF,
               b"TArrayD":                   TArrayD}
   
    for streamerinfo in reversed(streamerinfos):
        if streamerinfo.fName not in classes:
            if isinstance(streamerinfo.fName, str):
                classname = streamerinfo.fName
            else:
                classname = streamerinfo.fName.decode("ascii")
            code = ["    @staticmethod", "    def _readinto(self, source, cursor, context):", "        start, cnt, vers = _startcheck(source, cursor)"]

            bases = []
            formats = {}
            dtypes = {}
            basicnames = []
            basicletters = ""
            for elementi, element in enumerate(streamerinfo.fElements):
                if isinstance(element, TStreamerArtificial):
                    raise NotImplementedError

                elif isinstance(element, TStreamerBase):
                    code.append("        {0}._readinto(self, source, cursor, context)".format(element.fName))
                    bases.append(element.fName)

                elif isinstance(element, TStreamerBasicPointer):
                    if isinstance(element.fName, str):
                        name = element.fName
                    else:
                        name = element.fName.decode("ascii")
                    formatnum = len(formats) + 1
                    formats["_format{0}".format(formatnum)] = "struct.Struct('!B')"
                    code.append("        _{0} = cursor.field(source, {1}._format{2})".format(name, classname, formatnum))

                    m = re.search("\[([^\]]*)\]", element.fTitle.decode("ascii"))
                    if m is None:
                        raise ValueError("TStreamerBasicPointer fTitle should have a counter name between brackets: {0}".format(repr(element.fTitle)))
                    counter = m.group(1)

                    assert uproot.const.kOffsetP < element.fType < uproot.const.kOffsetP + 20
                    fType = element.fType - uproot.const.kOffsetP

                    dtypename = "_dtype{0}".format(len(dtypes) + 1)
                    if fType == uproot.const.kBool:
                        dtypes[dtypename] = "numpy.dtype(numpy.bool_)"
                    elif fType == uproot.const.kChar:
                        dtypes[dtypename] = "numpy.dtype('i1')"
                    elif fType == uproot.const.kUChar:
                        dtypes[dtypename] = "numpy.dtype('u1')"
                    elif fType == uproot.const.kShort:
                        dtypes[dtypename] = "numpy.dtype('>i2')"
                    elif fType == uproot.const.kUShort:
                        dtypes[dtypename] = "numpy.dtype('>u2')"
                    elif fType == uproot.const.kInt:
                        dtypes[dtypename] = "numpy.dtype('>i4')"
                    elif fType in (uproot.const.kBits, uproot.const.kUInt, uproot.const.kCounter):
                        dtypes[dtypename] = "numpy.dtype('>u4')"
                    elif fType == uproot.const.kLong:
                        dtypes[dtypename] = "numpy.dtype(numpy.long).newbyteorder('>')"
                    elif fType == uproot.const.kULong:
                        dtypes[dtypename] = "numpy.dtype(numpy.ulong).newbyteorder('>')"
                    elif fType == uproot.const.kLong64:
                        dtypes[dtypename] = "numpy.dtype('>i8')"
                    elif fType == uproot.const.kULong64:
                        dtypes[dtypename] = "numpy.dtype('>u8')"
                    elif fType in (uproot.const.kFloat, uproot.const.kFloat16):
                        dtypes[dtypename] = "numpy.dtype('>f4')"
                    elif fType in (uproot.const.kDouble, uproot.const.kDouble32):
                        dtypes[dtypename] = "numpy.dtype('>f8')"
                    else:
                        raise NotImplementedError(fType)
                    code.append("        self.{0} = cursor.array(source, self.{1}, self.{2})".format(name, counter, dtypename))

                elif isinstance(element, TStreamerBasicType):
                    if element.fArrayLength == 0:
                        basicnames.append("self." + element.fName.decode("ascii"))
                        if element.fType == uproot.const.kBool:
                            basicletters += "?"
                        elif element.fType == uproot.const.kChar:
                            basicletters += "b"
                        elif element.fType == uproot.const.kUChar:
                            basicletters += "B"
                        elif element.fType == uproot.const.kShort:
                            basicletters += "h"
                        elif element.fType == uproot.const.kUShort:
                            basicletters += "H"
                        elif element.fType == uproot.const.kInt:
                            basicletters += "i"
                        elif element.fType in (uproot.const.kBits, uproot.const.kUInt, uproot.const.kCounter):
                            basicletters += "I"
                        elif element.fType == uproot.const.kLong:
                            basicletters += "l"
                        elif element.fType == uproot.const.kULong:
                            basicletters += "L"
                        elif element.fType == uproot.const.kLong64:
                            basicletters += "q"
                        elif element.fType == uproot.const.kULong64:
                            basicletters += "Q"
                        elif element.fType in (uproot.const.kFloat, uproot.const.kFloat16):
                            basicletters += "f"
                        elif element.fType in (uproot.const.kDouble, uproot.const.kDouble32):
                            basicletters += "d"
                        else:
                            raise NotImplementedError(element.fType)

                        if elementi + 1 == len(streamerinfo.fElements) or not isinstance(streamerinfo.fElements[elementi + 1], TStreamerBasicType) or streamerinfo.fElements[elementi + 1].fArrayLength != 0:
                            formatnum = len(formats) + 1
                            formats["_format{0}".format(formatnum)] = "struct.Struct('!{0}')".format(basicletters)

                            if len(basicnames) == 1:
                                code.append("        {0} = cursor.field(source, {1}._format{2})".format(basicnames[0], classname, formatnum))
                            else:
                                code.append("        {0} = cursor.fields(source, {1}._format{2})".format(", ".join(basicnames), classname, formatnum))

                            basicnames = []
                            basicletters = ""

                    else:
                        raise NotImplementedError(element.fArrayLength)

                elif isinstance(element, TStreamerLoop):
                    raise NotImplementedError

                elif isinstance(element, TStreamerObjectAnyPointer):
                    raise NotImplementedError

                elif isinstance(element, TStreamerObjectPointer):
                    if element.fType == uproot.const.kObjectp:
                        code.append("        {0} = {1}.read(source, cursor, context)".format(element.fName, element.fTypeName.rstrip("*")))
                    elif element.fType == uproot.const.kObjectP:
                        code.append("        {0} = _readanyref(source, cursor, context)".format(element.fName))
                    else:
                        raise NotImplementedError

                elif isinstance(element, TStreamerSTL):
                    raise NotImplementedError

                elif isinstance(element, TStreamerSTLString):
                    raise NotImplementedError

                elif isinstance(element, (TStreamerObject, TStreamerObjectAny, TStreamerString)):
                    code.append("        {0} = {1}.read(source, cursor, context)".format(element.fName, element.fTypeName))

                else:
                    raise AssertionError

            code.append("        _endcheck(start, cursor, cnt)")

            if len(bases) == 0:
                bases.append("ROOTStreamedObject")

            for n, v in formats.items():
                code.append("    {0} = {1}".format(n, v))
            for n, v in dtypes.items():
                code.append("    {0} = {1}".format(n, v))

            code.insert(0, "class {0}({1}):".format(classname, ", ".join(bases)))
            classes[classname] = _makeclass(classname, id(streamerinfo), "\n".join(code), classes)

    return classes

def _makeclass(classname, id, codestr, classes):
    env = {}
    env.update(globals())
    env.update(classes)
    exec(compile(codestr, "<generated from TStreamerInfo {0} at 0x{1:012x}>".format(repr(classname), id), "exec"), env)
    env[classname]._codestr = codestr
    return env[classname]

################################################################ built-in ROOT objects for bootstrapping up to streamed classes

class ROOTObject(object):
    @classmethod
    def read(cls, source, cursor, context):
        out = cls.__new__(cls)
        cls._readinto(out, source, cursor, context)
        return out

    @staticmethod
    def _readinto(self, source, cursor, context):
        raise NotImplementedError

    def __repr__(self):
        if hasattr(self, "fName"):
            return "<{0} {1} at 0x{2:012x}>".format(self.__class__.__name__, repr(self.fName), id(self))
        else:
            return "<{0} at 0x{1:012x}>".format(self.__class__.__name__, id(self))

class TKey(ROOTObject):
    """Represents a key; for seeking to an object in a ROOT file.

        * `key.get()` to extract an object (initiates file-reading and possibly decompression).

    See `https://root.cern/doc/master/classTKey.html` for field definitions.
    """

    @staticmethod
    def _readinto(self, source, cursor, context):
        self.fNbytes, self.fVersion, self.fObjlen, self.fDatime, self.fKeylen, self.fCycle = cursor.fields(source, self._format1)
        if self.fVersion <= 1000:
            self.fSeekKey, self.fSeekPdir = cursor.fields(source, self._format2_small)
        else:
            self.fSeekKey, self.fSeekPdir = cursor.fields(source, self._format2_big)

        self.fClassName = cursor.string(source)
        self.fName = cursor.string(source)
        if self.fSeekPdir == 0:
            assert source.data(cursor.index, cursor.index + 1)[0] == 0
            cursor.skip(1)     # Top TDirectory fName and fTitle...
        self.fTitle = cursor.string(source)
        if self.fSeekPdir == 0:
            assert source.data(cursor.index, cursor.index + 1)[0] == 0
            cursor.skip(1)     # ...are prefixed *and* null-terminated! Both!

        # object size != compressed size means it's compressed
        if self.fObjlen != self.fNbytes - self.fKeylen:
            self._source = CompressedSource(context.compression, source, Cursor(self.fSeekKey + self.fKeylen), self.fNbytes - self.fKeylen, self.fObjlen)
            self._cursor = Cursor(0, origin=-self.fKeylen)

        # otherwise, it's uncompressed
        else:
            self._source = source
            self._cursor = Cursor(self.fSeekKey + self.fKeylen, origin=self.fSeekKey)

        self._context = context

    _format1       = struct.Struct("!ihiIhh")
    _format2_small = struct.Struct("!ii")
    _format2_big   = struct.Struct("!qq")

    def get(self):
        """Extract the object this key points to.

        Objects are not read or decompressed until this function is explicitly called.
        """
        if self.fClassName == b"TDirectory":
            return ROOTDirectory.read(self._source, self._cursor, self._context, self)

        elif self.fClassName in self._context.classes:
            return self._context.classes[self.fClassName].read(self._source, self._cursor.copied(), self._context)

        else:
            return Undefined(self._source, self._cursor.copied(), self._context)

class TStreamerInfo(ROOTObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        self.fName, _ = _nametitle(source, cursor)
        self.fCheckSum, self.fClassVersion = cursor.fields(source, TStreamerInfo._format)
        self.fElements = _readanyref(source, cursor, context)
        assert isinstance(self.fElements, list)
        _endcheck(start, cursor, cnt)

    _format = struct.Struct("!Ii")

    def format(self):
        return "StreamerInfo for class: {0}, version={1}, checksum=0x{2:08x}\n{3}{4}".format(self.fName, self.fClassVersion, self.fCheckSum, "\n".join("  " + x.format() for x in self.fElements), "\n" if len(self.fElements) > 0 else "")

class TStreamerElement(ROOTObject):
    @staticmethod
    def _readinto(self, source, cursor, context):    
        start, cnt, vers = _startcheck(source, cursor)

        self.fOffset = 0
        # https://github.com/root-project/root/blob/master/core/meta/src/TStreamerElement.cxx#L505
        self.fName, self.fTitle = _nametitle(source, cursor)
        self.fType, self.fSize, self.fArrayLength, self.fArrayDim = cursor.fields(source, TStreamerElement._format1)

        if vers == 1:
            n = cursor.field(source, TStreamerElement._format2)
            self.fMaxIndex = cursor.array(source, n, ">i4")
        else:
            self.fMaxIndex = cursor.array(source, 5, ">i4")

        self.fTypeName = cursor.string(source)

        if self.fType == 11 and (self.fTypeName == "Bool_t" or self.fTypeName == "bool"):
            self.fType = 18

        if vers <= 2:
            # FIXME
            # self.fSize = self.fArrayLength * gROOT->GetType(GetTypeName())->Size()
            pass

        self.fXmin, self.fXmax, self.fFactor = 0.0, 0.0, 0.0
        if vers == 3:
            self.fXmin, self.fXmax, self.fFactor = cursor.fields(source, TStreamerElement._format3)
        if vers > 3:
            # FIXME
            # if (TestBit(kHasRange)) GetRange(GetTitle(),fXmin,fXmax,fFactor)
            pass

        _endcheck(start, cursor, cnt)

    _format1 = struct.Struct("!iiii")
    _format2 = struct.Struct("!i")
    _format3 = struct.Struct("!ddd")

    def format(self):
        return "{0:15s} {1:15s} offset={2:3d} type={3:2d} {4}".format(self.fName, self.fTypeName, self.fOffset, self.fType, self.fTitle)

class TStreamerArtificial(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerArtificial, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerBase(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerBase, self)._readinto(self, source, cursor, context)
        if vers > 2:
            self.fBaseVersion = cursor.field(source, TStreamerBase._format)
        _endcheck(start, cursor, cnt)

    _format = struct.Struct("!i")

class TStreamerBasicPointer(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerBasicPointer, self)._readinto(self, source, cursor, context)
        self.fCountVersion = cursor.field(source, TStreamerBasicPointer._format)
        self.fCountName = cursor.string(source)
        self.fCountClass = cursor.string(source)
        _endcheck(start, cursor, cnt)

    _format = struct.Struct("!i")

class TStreamerBasicType(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerBasicType, self)._readinto(self, source, cursor, context)

        if uproot.const.kOffsetL < self.fType < uproot.const.kOffsetP:
            self.fType -= uproot.const.kOffsetL

        basic = True
        if self.fType in (uproot.const.kBool, uproot.const.kUChar, uproot.const.kChar):
            self.fSize = 1
        elif self.fType in (uproot.const.kUShort, uproot.const.kShort):
            self.fSize = 2
        elif self.fType in (uproot.const.kBits, uproot.const.kUInt, uproot.const.kInt, uproot.const.kCounter):
            self.fSize = 4
        elif self.fType in (uproot.const.kULong, uproot.const.kULong64, uproot.const.kLong, uproot.const.kLong64):
            self.fSize = 8
        elif self.fType in (uproot.const.kFloat, uproot.const.kFloat16):
            self.fSize = 4
        elif self.fType in (uproot.const.kDouble, uproot.const.kDouble32):
            self.fSize = 8
        elif self.fType == uproot.const.kCharStar:
            self.fSize = numpy.dtype(numpy.intp).itemsize
        else:
            basic = False

        if basic and self.fArrayLength > 0:
            self.fSize *= self.fArrayLength

        _endcheck(start, cursor, cnt)

class TStreamerLoop(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerLoop, self)._readinto(self, source, cursor, context)
        self.fCountVersion = cursor.field(source, TStreamerLoop._format)
        self.fCountName = cursor.string(source)
        self.fCountClass = cursor.string(source)
        _endcheck(start, cursor, cnt)

    _format = struct.Struct("!i")

class TStreamerObject(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerObject, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerObjectAny(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerObjectAny, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerObjectAnyPointer(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerObjectAnyPointer, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerObjectPointer(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerObjectPointer, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerSTL(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerSTL, self)._readinto(self, source, cursor, context)

        if vers > 2:
            # https://github.com/root-project/root/blob/master/core/meta/src/TStreamerElement.cxx#L1936
            raise NotImplementedError
        else:
            self.fSTLtype, self.fCtype = cursor.fields(source, TStreamerSTL._format)

        if self.fSTLtype == uproot.const.kSTLmultimap or self.fSTLtype == uproot.const.kSTLset:
            if self.fTypeName.startswith("std::set") or self.fTypeName.startswith("set"):
                self.fSTLtype = uproot.const.kSTLset
            elif self.fTypeName.startswith("std::multimap") or self.fTypeName.startswith("multimap"):
                self.fSTLtype = uproot.const.kSTLmultimap

        _endcheck(start, cursor, cnt)

    _format = struct.Struct("!ii")

class TStreamerSTLString(TStreamerSTL):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerSTLString, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

class TStreamerString(TStreamerElement):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        super(TStreamerString, self)._readinto(self, source, cursor, context)
        _endcheck(start, cursor, cnt)

################################################################ streamed classes (with some overrides)

class ROOTStreamedObject(ROOTObject): pass

class TObject(ROOTStreamedObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        _skiptobj(source, cursor)

class TString(str, ROOTStreamedObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        return TString(cursor.string(source))

class TNamed(TObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        TObject._readinto(self, source, cursor, context)
        self.fName = cursor.string(source)
        self.fTitle = cursor.string(source)
        _endcheck(start, cursor, cnt)

class TObjArray(list, ROOTStreamedObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        _skiptobj(source, cursor)
        name = cursor.string(source)
        size, low = cursor.fields(source, struct.Struct("!ii"))
        self.extend([_readanyref(source, cursor, context) for i in range(size)])
        _endcheck(start, cursor, cnt)

class TList(list, ROOTStreamedObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        _skiptobj(source, cursor)
        name = cursor.string(source)
        size = cursor.field(source, struct.Struct("!i"))
        self.extend([_readanyref(source, cursor, context) for i in range(size)])
        _endcheck(start, cursor, cnt)

class TArray(list, ROOTStreamedObject):
    @staticmethod
    def _readinto(self, source, cursor, context):
        length = cursor.field(source, TArray._format)
        self.extend(cursor.array(source, length, self._dtype))
    _format = struct.Struct("!i")

class TArrayC(TArray):
    _dtype = numpy.dtype(">i1")

class TArrayS(TArray):
    _dtype = numpy.dtype(">i2")

class TArrayI(TArray):
    _dtype = numpy.dtype(">i4")

class TArrayL(TArray):
    _dtype = numpy.dtype(numpy.int_).newbyteorder(">")

class TArrayL64(TArray):
    _dtype = numpy.dtype(">i8")

class TArrayF(TArray):
    _dtype = numpy.dtype(">f4")

class TArrayD(TArray):
    _dtype = numpy.dtype(">f8")

class Undefined(ROOTStreamedObject):
    @staticmethod
    def _readinto(source, cursor, context):
        start, cnt, vers = _startcheck(source, cursor)
        cursor.skip(cnt - 2)
        _endcheck(start, cursor, cnt)