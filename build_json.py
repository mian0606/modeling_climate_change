"""离线构建前端 JSON：python build_json.py [--only climate yangtze carbon]。"""

import argparse
from contextlib import contextmanager
import io
from itertools import groupby
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen
import zipfile

import numpy as np

from climate_calculations import (
    aggregate, carbon_experiments, grid_selection, month_groups, scenario_data, yangtze_data,
)

ROOT = Path(__file__).resolve().parent
BASE = "https://dsc106-final-proj-189624919408-us-east-2-an.s3.us-east-2.amazonaws.com/"
INPUTS = {
    "climate": "scenariomip_cesm2.npz",
    "yangtze": "ls3mip_cesm2.npz",
    "carbon": "cesm2.npz",
}
BLOCK = 4 * 1024**2


class RemoteFile(io.RawIOBase):
    """可随机定位的 S3 ZIP 文件，复用已有压缩块缓存。"""

    def __init__(self, url):
        self.url = url
        self.name_on_s3 = Path(url).name
        self.pos = 0
        with urlopen(Request(url, method="HEAD"), timeout=60) as response:
            self.size = int(response.headers["Content-Length"])
            self.etag = response.headers.get("ETag")
        version = self.etag.strip('"').replace("/", "_") if self.etag else str(self.size)
        self.cache = ROOT / ".cache" / self.name_on_s3 / version
        self.cache.mkdir(parents=True, exist_ok=True)

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        start = (0, self.pos, self.size)[whence]
        if start + offset < 0:
            raise ValueError("negative seek")
        self.pos = start + offset
        return self.pos

    def block_file(self, index):
        start = index * BLOCK
        stop = min(self.size, start + BLOCK)
        path = self.cache / str(index)
        if path.exists() and path.stat().st_size == stop - start:
            return path
        headers = {"Range": f"bytes={start}-{stop - 1}"}
        if self.etag:
            headers["If-Match"] = self.etag
        request = Request(self.url, headers=headers)
        with urlopen(request, timeout=180) as response:
            expected = f"bytes {start}-{stop - 1}/{self.size}"
            if response.status != 206 or response.headers.get("Content-Range") != expected:
                raise OSError("S3 未返回所请求的字节范围")
            block = response.read(stop - start + 1)
        if len(block) != stop - start:
            raise EOFError("S3 返回的分块长度不完整")
        with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(block)
        try:
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        cached = sorted(
            (p for p in self.cache.iterdir() if p.name.isdigit()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in cached[256:]:
            old.unlink(missing_ok=True)
        if index % 32 == 0:
            print(f"S3 {self.name_on_s3}: 压缩文件位置 {start / 1024**3:.2f} GiB", flush=True)
        return path

    def read(self, size=-1):
        if self.closed:
            raise ValueError("closed file")
        end = self.size if size < 0 else min(self.size, self.pos + size)
        parts = []
        while self.pos < end:
            index, offset = divmod(self.pos, BLOCK)
            length = min(end - self.pos, BLOCK - offset)
            with self.block_file(index).open("rb") as stream:
                stream.seek(offset)
                data = stream.read(length)
            if len(data) != length:
                raise EOFError("压缩块缓存长度不完整")
            parts.append(data)
            self.pos += length
        return b"".join(parts)


def open_source(value):
    if value.startswith("https://"):
        return RemoteFile(value)
    path = Path(value)
    if path.exists():
        return path.open("rb")
    if path.parent == ROOT and path.name in INPUTS.values():
        return RemoteFile(BASE + path.name)
    raise FileNotFoundError(path)


class Archive:
    def __init__(self, source):
        self.entries = zipfile.ZipFile(source)
        try:
            source.seek(0)
            self.indexes = np.load(source, allow_pickle=False)
        except Exception:
            self.entries.close()
            raise

    def close(self):
        self.indexes.close()
        self.entries.close()

    def has_group(self, group):
        return f"{group}__data.npy" in self.entries.namelist()

    def coordinates(self, group):
        return tuple(self.indexes[f"{group}__{axis}"] for axis in ("lat", "lon", "time"))

    def fields(self, group, fields, iy, ix, dimensions):
        names = self.indexes[f"{group}__variable_names"].tolist()
        if len(set(names)) != len(names):
            raise ValueError(f"{group} 存在重复变量名")
        targets = sorted((names.index(name), field, name)
                         for field, name in fields.items() if name in names)
        if not targets:
            raise ValueError(f"{group} 无所需变量")
        with self.entries.open(f"{group}__data.npy") as stream:
            version = np.lib.format.read_magic(stream)
            readers = {(1, 0): np.lib.format.read_array_header_1_0,
                       (2, 0): np.lib.format.read_array_header_2_0}
            if version not in readers:
                raise ValueError(f"不支持 NPY 版本 {version}")
            shape, fortran, dtype = readers[version](stream)
            expected = (len(names), *dimensions)
            if fortran or shape != expected or dtype.kind not in "fiu":
                raise ValueError(f"{group} 需要 C 顺序数值数组 {expected}，实际 {shape}")
            plane_bytes = shape[2] * shape[3] * dtype.itemsize
            field_bytes = shape[1] * plane_bytes
            subset = np.ix_(iy, ix)

            def read_plane():
                data = stream.read(plane_bytes)
                if len(data) != plane_bytes:
                    raise EOFError(f"{group} 的月格点数据不完整")
                return np.frombuffer(data, dtype=dtype).reshape(shape[2:])[subset].ravel()

            previous = 0
            for index, field, name in targets:
                skip = (index - previous) * field_bytes
                print(f"{group} → {name}，前置变量解压跳过 {skip / 1024**3:.2f} GiB", flush=True)
                if self.entries.getinfo(f"{group}__data.npy").compress_type == zipfile.ZIP_STORED:
                    stream.seek(skip, io.SEEK_CUR)
                else:
                    while skip:
                        size = min(skip, 16 * 1024**2)
                        if len(stream.read(size)) != size:
                            raise EOFError(f"{group} 的压缩数据提前结束")
                        skip -= size
                planes = (read_plane() for _ in range(shape[1]))
                yield field, planes
                for _ in planes:
                    pass
                previous = index + 1

# Exact versions from the original NPZ export metadata.
ZARR_ROOT = "cmip6/CMIP6"
ZARR_GROUPS = {
    **{key: ("ScenarioMIP", 2015, 2100, dict.fromkeys(
        ("Amon__tas", "Lmon__mrsos"), "v20190730"))
       for key in ("ssp585", "ssp370", "ssp245", "ssp126")},
    **{f"{key}__ocean": ("ScenarioMIP", 2015, 2100, {"Omon__zos": "v20190730"})
       for key in ("ssp585", "ssp370", "ssp245", "ssp126")},
    "land-hist": ("LS3MIP", 1850, 2015, dict.fromkeys(
        ("Amon__tas", "Amon__pr", "Lmon__mrsos", "Lmon__gpp", "Lmon__lai"), "v20190401")),
    "1pctCO2-bgc": ("C4MIP", 1, 150, {
        **dict.fromkeys(("Amon__tas", "Lmon__mrsos", "Lmon__mrso", "Emon__mrsol",
                         "Lmon__gpp", "Lmon__cVeg", "Emon__cSoil"), "v20190724"),
        "Amon__co2": "v20191105",
    }),
    "1pctCO2-rad": ("C4MIP", 1, 150, dict.fromkeys(
        ("Amon__tas", "Emon__mrsol", "Lmon__gpp", "Lmon__cVeg", "Emon__cSoil"), "v20210816")),
    "esm-ssp585": ("C4MIP", 2015, 2100, {
        **dict.fromkeys(("Amon__tas", "Lmon__cVeg", "Lmon__nbp"), "v20190801"),
        "Amon__co2": "v20191105",
    }),
}


class ZarrArchive:
    def __init__(self):
        # The NPZ fallback needs only NumPy.
        try:
            from gcsfs.core import GCSFileSystem
            from fsspec.implementations.asyn_wrapper import AsyncFileSystemWrapper
            from zarr.storage import FsspecStore
            import cftime
            import xarray as xr
        except ImportError as error:
            raise RuntimeError(
                'Zarr 依赖未安装。请运行 python -m pip install numpy "xarray>=2025.3" '
                'gcsfs "zarr>=3,<4" cftime，或用 --source npz。'
            ) from error
        fs = GCSFileSystem(token="anon", session_kwargs={"trust_env": True})
        self.fs = AsyncFileSystemWrapper(fs, asynchronous=True)
        self.store = FsspecStore
        self.xr = xr

    def has_group(self, group):
        return group in ZARR_GROUPS

    @contextmanager
    def open_variable(self, group, table, variable, version):
        activity = ZARR_GROUPS[group][0]
        experiment = group.removesuffix("__ocean")
        grid = "gr" if table == "Omon" else "gn"
        path = (f"{ZARR_ROOT}/{activity}/NCAR/CESM2/{experiment}/r1i1p1f1/"
                f"{table}/{variable}/{grid}/{version}")
        store = self.store(fs=self.fs, path=path, read_only=True)
        with self.xr.open_zarr(
            store, consolidated=True, chunks=None,
            decode_times=self.xr.coders.CFDatetimeCoder(use_cftime=True),
        ) as dataset:
            yield dataset[variable]

    def monthly_planes(self, data, calendar_months, lat_index, lon_index):
        dates = [(t.year, t.month) for t in data.time.values]
        date_index = {date: i for i, date in enumerate(dates)}
        if len(date_index) != len(dates) or dates != sorted(dates):
            raise ValueError("源数据的年月重复或顺序异常")
        if data.time.encoding.get("calendar") not in ("noleap", "365_day"):
            raise ValueError("数据需要无闰日历")
        chunk_size = data.encoding["preferred_chunks"]["time"]
        indexes = [date_index.get(date, -1) for date in calendar_months]
        layers = data.sizes.get("depth", 1)
        missing = np.full(layers * len(lat_index) * len(lon_index), np.nan)
        # A block is requested once, even when several depths or only summer months are needed.
        for chunk, entries in groupby(indexes, key=lambda i: i // chunk_size):
            selected = list(entries)
            if chunk == -1:
                for _ in selected:
                    yield missing
                continue
            block = data.isel(time=selected, lat=lat_index, lon=lon_index).values
            if "plev" in data.dims:
                block = block.mean(axis=1)
            for plane in block:
                yield plane.ravel()
            del plane, block

    def calculate_zarr_fields(self, group, fields, months, box):
        _, start, end, versions = ZARR_GROUPS[group]
        months = tuple(months)
        calendar_months = [(y, m) for y in range(start, end + 1) for m in months]
        times = [f"{y:04}-{m:02}" for y, m in calendar_months]
        years, groups = month_groups(times, months)
        by_variable = {}
        for key, name in fields.items():
            table, variable, *depth_suffix = name.split("__")
            source_name = f"{table}__{variable}"
            if source_name in versions:
                by_variable.setdefault(source_name, []).append((key, depth_suffix))

        values = {}
        lat = lon = None
        for source_name, outputs in by_variable.items():
            table, variable = source_name.split("__")
            version = versions[source_name]
            print(f"{group} → {source_name} / {version}", flush=True)
            with self.open_variable(group, table, variable, version) as data:
                source_lat = data.lat.values
                source_lon = (data.lon.values + 180) % 360 - 180
                iy, ix = grid_selection(source_lat, source_lon, box)
                if lat is None:
                    lat, lon = source_lat[iy], source_lon[ix]
                elif (lat.shape != iy.shape or lon.shape != ix.shape
                      or not np.allclose(lat, source_lat[iy], atol=1e-5, rtol=0)
                      or not np.allclose(lon, source_lon[ix], atol=1e-5, rtol=0)):
                    raise ValueError(f"{group}/{source_name} 格点不一致")

                axes = ["time"]
                if "plev" in data.dims:
                    pressure = data.plev.values
                    selected = np.flatnonzero((pressure >= 85000) & (pressure <= 100000))
                    if data.plev.attrs.get("units") != "Pa" or len(selected) != 3:
                        raise ValueError(f"{group}/{source_name} 缺少原有 CO₂ 气压层")
                    data = data.isel(plev=selected)
                    axes.append("plev")
                if outputs[0][1]:
                    if data.depth.attrs.get("units") != "m":
                        raise ValueError(f"{group}/{source_name} 土壤深度必须以米为单位")
                    matched = []
                    for _, (suffix,) in outputs:
                        depth = int(suffix.removeprefix("depth_").removesuffix("cm")) / 100
                        selected = np.flatnonzero(np.isclose(data.depth.values, depth, atol=1e-5))
                        if len(selected) != 1:
                            raise ValueError(f"{group}/{source_name} 缺少 {suffix}")
                        matched.append(int(selected[0]))
                    data = data.isel(depth=matched)
                    axes.append("depth")
                data = data.transpose(*axes, "lat", "lon")
                planes = self.monthly_planes(data, calendar_months, iy, ix)
                count = len(iy) * len(ix)
                # All requested depths of a variable share the same monthly-mean rule.
                aggregated = aggregate(planes, years, groups, count * len(outputs), outputs[0][0])
                for i, (key, _) in enumerate(outputs):
                    values[key] = aggregated[:, i * count:(i + 1) * count]
        return lat, lon, years, values


def clean(value):
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (np.floating, float)):
        value = float(value)
        if not np.isfinite(value):
            return None
        # 低量级变量如浓度保留有效数字。
        return float(f"{value:.7g}") if 0 < abs(value) < 0.001 else round(value, 3)
    if isinstance(value, np.integer):
        return int(value)
    return value


def emit(stream, value):
    if isinstance(value, dict):
        stream.write("{")
        for index, (key, item) in enumerate(value.items()):
            if index:
                stream.write(",")
            stream.write(json.dumps(key) + ":")
            emit(stream, item)
        stream.write("}")
    elif hasattr(value, "__next__"):
        stream.write("[")
        for index, item in enumerate(value):
            if index:
                stream.write(",")
            emit(stream, item)
        stream.write("]")
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        emit(stream, iter(value))
    else:
        stream.write(json.dumps(clean(value), ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False))


def write_carbon(stream, archive):
    stream.write('{"source":"NCAR CESM2 C4MIP r1i1p1f1","experiments":{')
    for index, (name, experiment) in enumerate(carbon_experiments(archive)):
        if index:
            stream.write(",")
        stream.write(json.dumps(name) + ":")
        emit(stream, experiment)
        print(f"已写出 {name} 的逐格数据", flush=True)
    stream.write("}}")


@contextmanager
def atomic_output(target):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                     suffix=".part", delete=False) as stream:
        temp = Path(stream.name)
        try:
            yield stream
            stream.close()
            temp.replace(target)
        finally:
            stream.close()
            temp.unlink(missing_ok=True)


def write_dataset(dataset, stream, archive):
    if dataset == "carbon":
        write_carbon(stream, archive)
    else:
        calculate = scenario_data if dataset == "climate" else yangtze_data
        emit(stream, calculate(archive))


def build(dataset, value, output_dir, zarr_archive=None):
    target = output_dir / f"{dataset}.json"
    with atomic_output(target) as stream:
        if value is None:
            write_dataset(dataset, stream, zarr_archive)
        else:
            with open_source(value) as source:
                archive = Archive(source)
                try:
                    write_dataset(dataset, stream, archive)
                finally:
                    archive.close()
    print(f"已保存 {target}（{target.stat().st_size / 1024**2:.1f} MiB）", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=INPUTS, default=list(INPUTS))
    parser.add_argument("--source", choices=("zarr", "npz"), default="zarr")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    for name in INPUTS:
        parser.add_argument(f"--{name}-npz")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    zarr_archive = None
    for dataset in dict.fromkeys(args.only):
        value = getattr(args, f"{dataset}_npz")
        if value is None and args.source == "npz":
            value = str(ROOT / INPUTS[dataset])
        if value is None and zarr_archive is None:
            zarr_archive = ZarrArchive()
        build(dataset, value, args.output_dir, zarr_archive)


if __name__ == "__main__":
    main()
