import xdrlib
import struct

class FileUnpacker(xdrlib.Unpacker):
  def __init__(self, fileHandle):
    self.file = fileHandle
    self.reset()

  def reset(self):
    self.file.seek(0)

  def get_position(self):
    return self.file.tell()

  def set_position(self, position):
    self.file.seek(position)

  def unpack_uint(self):
    data = self.file.read(4)
    if len(data) < 4:
      raise EOFError
    x = struct.unpack('>L', data)[0]
    try:
      return int(x)
    except OverflowError:
      return x

  def unpack_int(self):
    data = self.file.read(4)
    if len(data) < 4:
      raise EOFError
    return struct.unpack('>l', data)[0]

  def unpack_float(self):
    data = self.file.read(4)
    if len(data) < 4:
      raise EOFError
    return struct.unpack('>f', data)[0]

  def unpack_double(self):
    data = self.file.read(8)
    if len(data) < 8:
      raise EOFError
    return struct.unpack('>d', data)[0]

  def unpack_fp_scalar(self, precision):
    if precision == 8:
      return self.unpack_double()
    elif precision == 4:
      return self.unpack_float()
    else:
      raise ValueError('Error in specified precision when retrieving floating point')

  def unpack_fstring(self,n):
    if n<0:
      raise ValueError('fstring size must be nonnegative')
    data = self.file.read((n+3)//4*4).decode('utf-8')
    #if len(data) < n:
    #  raise EOFError
    return data[:n]

  def unpack_farray(self, n, unpack_item):
    ans = []
    for i in range(n):
      ans.append(unpack_item())
    return ans

  def unpack_fp_array(self, n, precision):
    ans = []
    for i in range(n):
      ans.append(self.unpack_fp_scalar(precision))
    return ans
