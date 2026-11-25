from pathlib import Path

from build_json import ZarrArchive, atomic_output, emit
from climate_calculations import carbon_experiments

output_dir = Path(__file__).resolve().parent

for experiment, data in carbon_experiments(ZarrArchive()):
    # 复用已经算好的年度数组。每个变量分别写出。
    frames = tuple(data["frames"])

    for variable, available in data["availability"].items():
        if not available:
            continue

        output = output_dir / f"carbon_{experiment}_{variable}.json"
        payload = {
            "experiment": experiment,
            "variable": variable,
            "sourceVariable": data["variables"][variable],
            "units": data["units"][variable],
            "calendar": data["calendar"],
            "yearType": data["yearType"],
            "years": data["years"],
            "lat": data["lat"],
            "lon": data["lon"],
            "values": (frame[variable] for frame in frames),
        }

        with atomic_output(output) as stream:
            emit(stream, payload)

        print(f"已保存 {output.name}（{output.stat().st_size / 1024**2:.1f} MiB）")