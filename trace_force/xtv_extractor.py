from . import xtvreader

class XtvExtractor:
    def __init__(self, filepath):
        self.filepath = filepath
        self._file_handle = open(filepath, "rb")
        self.xtv = xtvreader.XtvFile(self._file_handle, verbose=False)
        self.times = self.xtv.times

    def close(self):
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_cell_variable(self, comp_id, cell_idx, var_name):
        """
        Extracts the time series of a cell-centered variable (e.g. pn, alpn, roln, rovn, rom, vol, fa).
        Returns a list of float values corresponding to the simulation times.
        """
        channel = f"{var_name}-{comp_id}A{cell_idx:02d}"
        try:
            vec = self.xtv.getTimeVector(channel)
            return [val for t, val in vec]
        except Exception as e:
            raise KeyError(f"Failed to extract cell variable '{channel}': {e}")

    def get_edge_variable(self, comp_id, edge_idx, var_name):
        """
        Extracts the time series of an edge-centered variable (e.g. vln, vvn).
        Returns a list of float values corresponding to the simulation times.
        """
        channel = f"{var_name}-{comp_id}A{edge_idx:02d}"
        try:
            vec = self.xtv.getTimeVector(channel)
            return [val for t, val in vec]
        except Exception as e:
            raise KeyError(f"Failed to extract edge variable '{channel}': {e}")

    def has_variable(self, comp_id, var_name):
        """
        Checks if a component has a specific variable registered (e.g., to verify if wfl/wfv exist).
        """
        # Look up by tuple key (compId, compType)
        for (cid, ctype) in self.xtv.components.keys():
            if cid == comp_id:
                comp = self.xtv.components[(cid, ctype)]
                return var_name in comp.channels
        return False
