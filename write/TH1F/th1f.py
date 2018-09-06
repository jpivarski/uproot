import numpy

class TH1F(object):

    def __init__(self, nbinsx, xlow, xup):
        self.string = ""
        self.nbinsx = nbinsx
        self.xlow = xlow
        self.xup = xup

    def values1(self):
        bytestream = [ 64,   0,  66,  54,   0,   2,  64,   0,  64, 148,   0,   8,  64,
          0,   0,  28,   0,   1,   0,   1,   0,   0,   0,   0,   3,   0,
          0,   8,   4, 116, 104,  49, 102,  10, 116, 104,  49, 102,  32,
        116, 105, 116, 108, 101,  64,   0,   0,   8,   0,   2,   2,  90,
          0,   1,   0,   1,  64,   0,   0,   6,   0,   2,   0,   0,   3,
        233,  64,   0,   0,  10,   0,   2,   0,   1,   0,   1,  63, 128,
          0,   0,   0,   0,   0, 102,  64,   0,   0, 109,   0,  10,  64,
          0,   0,  19,   0,   1,   0,   1,   0,   0,   0,   0,   3,   0,
          0,   0,   5, 120,  97, 120, 105, 115,   0,  64,   0,   0,  36,
          0,   4,   0,   0,   1, 254,   0,   1,   0,   1,   0,  42,  59,
        163, 215,  10,  61,  15,  92,  41,  60, 245, 194, 143,  63, 128,
          0,   0,  61,  15,  92,  41,   0,   1,   0,  42,   0]
        return numpy.frombuffer(bytes(bytestream), dtype=numpy.uint8)

    def values2(self):
        return self.nbinsx, self.xlow, self.xup

    def values3(self):
        f = numpy.memmap("habla.root", mode = "r", dtype = numpy.uint8)
        bytestream = f[436:17237]
        return numpy.frombuffer(bytes(bytestream), dtype=numpy.uint8)