"""把 CESM2 月数据转换成页面需要的格点统计。"""

from itertools import groupby

import numpy as np

DAYS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
SCENARIOS = ("ssp585", "ssp370", "ssp245", "ssp126")
EXPERIMENTS = ("1pctCO2-bgc", "1pctCO2-rad", "esm-ssp585")
TAS = "Amon__tas"
YANGTZE_FIELDS = {
    "temperature": TAS, "soil": "Lmon__mrsos", "gpp": "Lmon__gpp",
    "precipitation": "Amon__pr", "lai": "Lmon__lai", "nbp": "Lmon__nbp",
}
CARBON_FIELDS = {
    "temperature": TAS, "co2": "Amon__co2", "soil": "Lmon__mrsos",
    "soilColumn": "Lmon__mrso",
    **{f"soil{z}cm": f"Emon__mrsol__depth_{z}cm" for z in (4, 26, 80)},
    "gpp": "Lmon__gpp", "vegetationCarbon": "Lmon__cVeg",
    "soilCarbon": "Emon__cSoil", "nbp": "Lmon__nbp",
}
UNITS = {
    "temperature": "°C", "soil": "kg m⁻²", "soilColumn": "kg m⁻²",
    **{f"soil{z}cm": "kg m⁻² (layer water mass)" for z in (4, 26, 80)},
    "precipitation": "mm", "lai": "m² m⁻²", "gpp": "g C m⁻²",
    "nbp": "g C m⁻²", "vegetationCarbon": "kg C m⁻²",
    "soilCarbon": "kg C m⁻²", "co2": "same units as Amon__co2 in source",
}

STORIES = {
    "california": (-125, -114, 32, 42), "india": (68, 89, 7, 35),
    "china": (100, 122, 24, 35), "amazon": (-74, -50, -15, 5),
    "southeast": (102, 127, 8, 22),
}
REGIONS = {
    "north_america": (-170, -50, 15, 75), "south_america": (-82, -34, -56, 14),
    "europe": (-15, 40, 35, 72), "north_africa": (-18, 40, 15, 35),
    "sub_saharan": (-18, 52, -35, 15), "west_asia": (40, 65, 12, 50),
    "south_asia": (65, 90, 5, 35), "east_asia": (90, 145, 20, 55),
    "southeast_asia": (90, 145, -10, 20), "oceania": (110, 180, -50, -10),
}


def month_groups(times, months):
    result = []
    # 输入是按时间排列的平面。C4MIP 首月有两个时间戳，归为同一个月。
    for date, items in groupby(enumerate(times), key=lambda item: str(item[1])[:7]):
        year, month = map(int, date.split("-"))
        indices = [i for i, _ in items]
        if month in months:
            result.append((year, month, indices))
    years = sorted({year for year, _, _ in result})
    if not years:
        raise ValueError("没有可计算的月份")
    expected = [(year, month) for year in range(years[0], years[-1] + 1) for month in months]
    if [(year, month) for year, month, _ in result] != expected:
        raise ValueError("月份不完整或时间未按顺序排列")
    return years, result


def aggregate(planes, years, groups, columns, field):
    result = np.empty((len(years), columns), dtype=np.float64)
    cursor = 0
    total = np.zeros(columns)
    days_in_period = 0
    for year, month, indices in groups:
        for _ in range(cursor, indices[0]):
            next(planes)
        values = next(planes).astype(np.float64, copy=True)
        for _ in indices[1:]:
            other = next(planes)
            if np.any(np.isfinite(values) & np.isfinite(other) & (values != other)):
                raise ValueError(f"{field} {year:04}-{month:02} 有互相冲突的重复月值")
            values = np.where(np.isfinite(values), values, other)
        cursor = indices[-1] + 1
        days = DAYS[month - 1]
        # 普通加法传播 NaN。仅同月的重复时间戳可互补缺失数据。
        total += values * days
        days_in_period += days
        if month == groups[-1][1]:
            if field in ("precipitation", "gpp", "nbp"):
                scale = 86400 if field == "precipitation" else 86400 * 1000
                result[year - years[0]] = total * scale
            else:
                result[year - years[0]] = total / days_in_period
            total.fill(0)
            days_in_period = 0
    if field == "temperature":
        result -= 273.15
    return result


def grid_selection(lat, lon, box):
    iy = np.argsort(lat)
    ix = np.argsort(lon)
    if box is not None:
        west, east, south, north = box
        iy = iy[(lat[iy] >= south) & (lat[iy] <= north)]
        ix = ix[(lon[ix] >= west) & (lon[ix] <= east)]
    if not len(iy) or not len(ix):
        raise ValueError("区域内没有格点")
    return iy, ix


def calculate_fields(archive, group, fields, months, box=None):
    if hasattr(archive, "calculate_zarr_fields"):
        return archive.calculate_zarr_fields(group, fields, months, box)
    lat, lon, times = archive.coordinates(group)
    lon = (lon + 180) % 360 - 180
    iy, ix = grid_selection(lat, lon, box)
    years, groups = month_groups(times, months)
    values = {}
    for key, planes in archive.fields(group, fields, iy, ix, (len(times), len(lat), len(lon))):
        values[key] = aggregate(planes, years, groups, len(iy) * len(ix), key)
    return lat[iy], lon[ix], years, values


def reference(values, years, start, end):
    indices = [i for i, year in enumerate(years) if start <= year <= end]
    if len(indices) != end - start + 1:
        raise ValueError(f"缺少完整基准期 {start}–{end}")
    return values[indices].mean(axis=0)


def masked_average(field, weights, mask):
    good = np.isfinite(field) & mask
    return float(np.average(field[good], weights=weights[good])) if good.any() else np.nan


def spatial_weights(lat, lon):
    edges = np.r_[-90, (lat[:-1] + lat[1:]) / 2, 90]
    return np.broadcast_to(np.diff(np.sin(np.deg2rad(edges)))[:, None], (len(lat), len(lon)))


def coarse_grid(field, weights):
    ny, nx = (n // 4 for n in weights.shape)
    grid = field[:ny * 4, :nx * 4].reshape(ny, 4, nx, 4)
    w = weights[:ny * 4, :nx * 4].reshape(grid.shape)
    good = np.isfinite(grid)
    num = np.where(good, grid * w, 0).sum(axis=(1, 3))
    den = np.where(good, w, 0).sum(axis=(1, 3))
    return np.divide(num, den, out=np.full_like(num, np.nan), where=den > 0).ravel()


SCENARIO_FIELDS = {
    "temperature": TAS, "soil": "Lmon__mrsos", "seaLevel": "Omon__zos",
}
SCENARIO_UNITS = {"temperature": "°C", "soil": "kg/m²", "seaLevel": "cm"}


def scenario_summary(field, weights, masks):
    return {
        "global": masked_average(field, weights, True),
        **{
            kind: {key: masked_average(field, weights, mask) for key, mask in group.items()}
            for kind, group in masks.items()
        },
    }


def scenario_grid(lat, lon):
    weights = spatial_weights(lat, lon)
    masks = {
        kind: {
            key: (lon[None, :] >= west) & (lon[None, :] < east)
                 & (lat[:, None] >= south) & (lat[:, None] < north)
            for key, (west, east, south, north) in boxes.items()
        }
        for kind, boxes in (("regions", REGIONS), ("stories", STORIES))
    }
    coarse = {axis: values[:len(values) // 4 * 4].reshape(-1, 4).mean(axis=1)
              for axis, values in (("lat", lat), ("lon", lon))}
    return weights, masks, coarse


def scenario_data(archive):
    payload = {
        "source": "NCAR CESM2 · ScenarioMIP · r1i1p1f1",
        "baseline": "2015–2034",
        "units": dict.fromkeys(("temperature", "warming", "grid", "avoidedWarming"), "°C"),
        "indicators": {
            key: {"variable": name, "unit": SCENARIO_UNITS[key],
                  "grid": "ocean" if key == "seaLevel" else "atmosphere"}
            for key, name in SCENARIO_FIELDS.items()
        },
        "grids": {},
        "regionDefinitions": {"stories": STORIES, "regions": REGIONS},
        "regionNote": "Regional means use valid native grid cells within geographic boxes.",
        "scenarios": {},
    }
    max_warming = 1
    high = {key: {} for key in SCENARIO_FIELDS}
    baselines = {}
    native = {}
    grids = {}
    for group in SCENARIOS:
        land_fields = {key: name for key, name in SCENARIO_FIELDS.items() if key != "seaLevel"}
        lat, lon, years, values = calculate_fields(archive, group, land_fields, range(1, 13))
        if "temperature" not in values:
            raise ValueError(f"{group} 缺少 {TAS}")
        group_grids = {"atmosphere": (lat, lon)}
        ocean_group = f"{group}__ocean"
        if archive.has_group(ocean_group):
            olat, olon, ocean_years, ocean = calculate_fields(
                archive, ocean_group, {"seaLevel": SCENARIO_FIELDS["seaLevel"]}, range(1, 13)
            )
            if ocean_years != years:
                raise ValueError(f"{group} 海洋和陆地年份不一致")
            group_grids["ocean"] = (olat, olon)
            values["seaLevel"] = ocean["seaLevel"] * 100

        for key, (ylat, xlon) in group_grids.items():
            if key in native:
                if not (np.array_equal(ylat, native[key][0]) and np.array_equal(xlon, native[key][1])):
                    raise ValueError(f"{group} 的 {key} 格点与其他情景不一致")
            else:
                native[key] = (ylat, xlon)
                weights, masks, coarse = scenario_grid(ylat, xlon)
                grids[key] = (weights, masks)
                payload["grids"][key] = coarse

        if group == "ssp585":
            baselines = {key: reference(value, years, 2015, 2034) for key, value in values.items()}
            payload.update(payload["grids"]["atmosphere"])

        frames = [{"indicators": {"soil": None, "seaLevel": None}} for _ in years]
        for key, value in values.items():
            if key not in baselines:
                continue
            weights, masks = grids[payload["indicators"][key]["grid"]]
            annual = value.reshape(-1, *weights.shape)
            anomalies = annual - baselines[key].reshape(weights.shape)
            for i, year in enumerate(years):
                smoothed = anomalies[max(0, i - 4):i + 6].mean(axis=0)
                grid = coarse_grid(smoothed, weights)
                if group == "ssp585":
                    high[key][year] = grid
                difference = high[key][year] - grid if year in high[key] else None
                summary = scenario_summary(smoothed, weights, masks)
                if key == "temperature":
                    finite = [v for v in summary["regions"].values() if np.isfinite(v)]
                    max_warming = max([max_warming, *finite])
                    frames[i].update({
                        "temperature": coarse_grid(annual[i], weights),
                        "warming": coarse_grid(anomalies[i], weights),
                        "grid": grid,
                        "avoidedWarming": difference,
                        **summary,
                        "regionalSpread": max(finite) - min(finite) if len(finite) >= 2 else None,
                    })
                else:
                    frames[i]["indicators"][key] = {
                        "value": coarse_grid(annual[i], weights),
                        "grid": grid, "differenceFromHigh": None if difference is None else -difference,
                        **summary,
                    }
        print(f"已计算 {group} 的地图和区域统计", flush=True)
        payload["scenarios"][group] = {"years": years, "frames": frames}
    payload["maxRegionalWarming"] = int(np.ceil(max_warming))
    return payload


def yangtze_data(archive):
    group = "land-hist"
    lat, lon, years, values = calculate_fields(
        archive, group, YANGTZE_FIELDS, (6, 7, 8), STORIES["china"]
    )
    missing = {"temperature", "soil", "gpp", "precipitation", "lai"} - values.keys()
    if missing:
        raise ValueError(f"{group} 缺少必要字段 {sorted(missing)}")
    baselines = {key: reference(value, years, 1981, 2010) for key, value in values.items()}
    frames = []
    for i, year in enumerate(years):
        entry = {key: values[key][i] if key in values else None for key in YANGTZE_FIELDS}
        frames.append({
            "year": year, **entry,
            "anomalies": {key: value - baselines[key] if value is not None else None
                          for key, value in entry.items()},
        })

    def domain(values):
        finite = values[np.isfinite(values)]
        return [finite.min(), finite.max()] if finite.size else [0, 1]

    return {
        "source": "CESM2 / LS3MIP / land-hist / r1i1p1f1",
        "season": "JJA", "calendar": "noleap", "baseline": [1981, 2010],
        "regionNote": "100–122°E、24–35°N 代表区域框，并非精确长江流域边界",
        "lat": lat, "lon": lon, "reference": baselines, "frames": frames,
        "units": {key: UNITS[key] for key in YANGTZE_FIELDS},
        "domains": {key: domain(value) for key, value in values.items()},
        "availability": {key: key in values for key in YANGTZE_FIELDS},
        "variables": YANGTZE_FIELDS,
    }


def carbon_experiments(archive):
    for group in EXPERIMENTS:
        lat, lon, years, values = calculate_fields(archive, group, CARBON_FIELDS, range(1, 13))
        yield group, {
            "calendar": "noleap",
            "yearType": "experiment" if group.startswith("1pct") else "calendar",
            "lat": lat, "lon": lon, "years": years,
            "variables": CARBON_FIELDS,
            "availability": {key: key in values for key in CARBON_FIELDS},
            "units": {key: UNITS[key] for key in CARBON_FIELDS},
            "frames": (
                {key: values[key][i] if key in values else None for key in CARBON_FIELDS}
                for i in range(len(years))
            ),
        }
