"""XTV header parser robustness tests.

XTV files are received from other teams and parsed before any validation an
analyst could apply, so the header parser must fail loudly on malformed input
rather than hang or allocate without bound. Each malformed case here is built
as raw bytes - no TRACE run is needed - and corresponds to a crafted-input
failure that previously either spun the header loop forever or sized an
allocation from unchecked integers in the Starting Block.

Written against the standard library's unittest so they run with no additional
dependencies:

    python3 -m unittest discover -s tests

They are ordinary TestCase classes, so pytest collects them unchanged if it is
available.
"""

import contextlib
import io
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_force.xtvreader import XtvFile, XTVError, err_codes  # noqa: E402


def xdr_int(value):
    return struct.pack(">l", value)


def xdr_string(text):
    data = text.encode("ascii")
    padded = data + b"\x00" * (-len(data) % 4)
    return struct.pack(">L", len(data)) + padded


STRIDE = 20  # bytes of per-edit bookkeeping before the time value


def starting_block(xtv_res=4, data_start=0, data_len=24, n_points=1):
    """The XTV Starting Block: a header string, 17 ints, 7 strings.

    Defaults describe a file with no components or channels, whose per-edit
    record is just the stride plus one time value (dataLen = 20 + xtvRes).
    """
    ints = [
        1,  # xtvMajorV
        0,  # xtvMinorV
        0,  # revNumber
        xtv_res,
        0,  # nUnits
        0,  # nComp
        0,  # nSVar
        0,  # nDVar
        0,  # nSChannels
        0,  # nDCannels
        data_start,
        data_len,
        n_points,
        0,  # status
        0, 0, 0,  # spares
    ]
    strings = ["MUX", "SI", "sys", "os", "date", "time", "title"]
    return (
        xdr_string("TRACE")
        + b"".join(xdr_int(i) for i in ints)
        + b"".join(xdr_string(s) for s in strings)
    )


def header_block(block_type, jump, payload=b""):
    return xdr_string(block_type) + xdr_int(0) + xdr_int(jump) + payload


def build_xtv(blocks=(), times=(0.0,), xtv_res=4, data_len=None,
              n_points=None, data_start=None):
    """Assemble a complete XTV byte stream.

    The header is built twice so dataStart can point at the first byte after
    it. Data records are stride bytes of padding followed by the time value.
    """
    if data_len is None:
        data_len = STRIDE + xtv_res
    if n_points is None:
        n_points = len(times)
    body = b"".join(blocks) + header_block("DATA", 0)
    if data_start is None:
        data_start = len(starting_block(xtv_res, 0, data_len, n_points)) + len(body)
    header = starting_block(xtv_res, data_start, data_len, n_points) + body
    fmt = ">d" if xtv_res == 8 else ">f"
    records = b"".join(b"\x00" * STRIDE + struct.pack(fmt, t) for t in times)
    return header + records


def open_xtv(data):
    handle = io.BytesIO(data)
    handle.name = "test.xtv"  # XTVError formats the file name into its message
    return XtvFile(handle)


class HeaderLivenessCase(unittest.TestCase):
    def assertXTVError(self, data, err_key):
        # The constructor prints diagnostics for some failures; keep them out
        # of the test run's output.
        with self.assertRaises(XTVError) as caught:
            with contextlib.redirect_stdout(io.StringIO()):
                open_xtv(data)
        self.assertIn(err_codes[err_key], str(caught.exception))

    def test_minimal_valid_file_parses(self):
        """The fixture itself must represent a well-formed file."""
        xtv = open_xtv(build_xtv(times=(0.0, 1.5)))
        self.assertEqual(xtv.times, [0.0, 1.5])

    def test_double_precision_valid_file_parses(self):
        xtv = open_xtv(build_xtv(times=(0.0, 2.5), xtv_res=8))
        self.assertEqual(xtv.times, [0.0, 2.5])

    def test_zero_jump_block_is_rejected(self):
        # A non-DATA block whose size is zero re-parses itself forever: the
        # loop's only exits are a DATA block or an exception, and every read
        # of the same block succeeds. Formerly a silent CPU spin.
        data = build_xtv(blocks=(header_block("GEND", 0),))
        self.assertXTVError(data, "BLOCK_JUMP_ERR")

    def test_negative_jump_block_is_rejected(self):
        data = build_xtv(blocks=(header_block("GEND", -16),))
        self.assertXTVError(data, "BLOCK_JUMP_ERR")

    def test_data_block_size_is_never_inspected(self):
        # DATA is terminal; its size field is unused and a zero there is fine.
        xtv = open_xtv(build_xtv())
        self.assertEqual(xtv.times, [0.0])

    def test_zero_data_len_is_rejected(self):
        # dataLen == 0 makes the per-edit seek offset constant and in-file, so
        # the EOFError backstop never fires and the times loop appends nPoints
        # floats - an allocation sized by an unchecked int, not by the file.
        # xtvRes = -20 keeps the header's record-length reconciliation
        # consistent (20 + (-20) == 0) so the check under test is reached.
        data = build_xtv(xtv_res=-20, data_len=0, n_points=2**31 - 1, times=())
        self.assertXTVError(data, "DATA_LEN_ERR")

    def test_oversized_n_points_is_rejected(self):
        # A record window extending past the end of the file can only end in
        # a failed read; reject it up front with a named error instead.
        data = build_xtv(n_points=2**20, times=(0.0,))
        self.assertXTVError(data, "DATA_BOUNDS_ERR")

    def test_negative_data_start_is_rejected(self):
        data = build_xtv(data_start=-4, times=(0.0,))
        self.assertXTVError(data, "DATA_BOUNDS_ERR")

    def test_record_length_mismatch_still_rejected(self):
        # The pre-existing reconciliation check must still fire before the
        # new bounds checks: a dataLen that disagrees with the header's
        # accumulated record length means every channel offset is wrong.
        data = build_xtv(data_len=28, times=(0.0,))
        self.assertXTVError(data, "RECORD_LEN_ERR")

    def test_empty_file_reports_starting_block_error(self):
        self.assertXTVError(b"", "HDR_UNPACK_ERR")

    def test_non_mux_format_rejected(self):
        data = build_xtv().replace(xdr_string("MUX"), xdr_string("XTV"), 1)
        self.assertXTVError(data, "HDR_FORMAT_ERR")

    def test_unordered_times_rejected(self):
        data = build_xtv(times=(1.0, 0.5))
        self.assertXTVError(data, "TIME_ORDER_ERR")


if __name__ == "__main__":
    unittest.main()
