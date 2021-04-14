#!/usr/bin/python3
import argparse
import csv
import datetime
import json
import numpy as np
import os
import pathlib

import matplotlib.dates as md
from matplotlib import pyplot as plt


def summary_parser():
    parser = argparse.ArgumentParser(
        "This tool can be used to generate a summary of the pageload numbers for a single "
        "given subtest, i.e. ContenfulSpeedIndex. We provide the summary through a geomean "
        "and you can also perform a comparison with competing browsers using "
        "`--compare-browsers`. You must provide data in the CSV format that is returned from "
        "this query: https://sql.telemetry.mozilla.org/queries/79289"
    )
    parser.add_argument("data", metavar="CSV_DATA", type=str,
                        help="The data to summarize.")
    parser.add_argument("--timespan", type=int, default=24,
                        help="Minimum time between each data point in hours.")
    parser.add_argument("--platforms", nargs="*", default=[],
                        help="Platforms to summarize. Default is all platforms.")
    parser.add_argument("--output", type=str, default=os.getcwd(),
                        help="This is where the data will be saved in JSON format. If the "
                        "path has a `.json` suffix then we'll use the part immediately "
                        "before it as the file name.")
    return parser


def open_csv_data(path):
    """Opens a CSV data file from a given path."""
    rows = []
    with path.open() as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    return rows


def get_data_ind(data, fieldname):
    """Returns an index for the requested field."""
    for i, entry in enumerate(data[0]):
        if fieldname in entry:
            return i
    return None


def organize_data(data, platforms):
    """Organizes the data into a format that is easier to handle."""

    platform_ind = get_data_ind(data, "platform")
    test_ind = get_data_ind(data, "suite")
    extra_ind = get_data_ind(data, "extra_options")
    tag_ind = get_data_ind(data, "tags")
    val_ind = get_data_ind(data, "value")
    time_ind = get_data_ind(data, "push_timestamp")
    app_ind = get_data_ind(data, "application")

    org_data = {}
    for entry in data[1:]:
        platform = entry[platform_ind]
        if platforms and platform not in platforms:
            continue

        test = entry[test_ind]
        app = entry[app_ind]
        extras = entry[extra_ind].split()
        tags = entry[tag_ind].split()
        variants = "e10s"
        pl_type = "cold"

        # Without this, we might start pulling in data
        # from mozperftest tests
        if "warm" not in extras and "cold" not in extras:
            continue

        # Make sure we always ignore live site data
        if "live" in extras:
            continue

        if "warm" in extras:
            pl_type = "warm"

        if "fission" in extras:
            variants += "fission-"
        if "webrender" in extras:
            variants += "webrender"

        # Newer data no longer has the nocondprof option
        if "nocondprof" in extras:
            extras.remove("nocondprof")
        # Older data didn't have this flag
        if "visual" not in extras:
            extras.append("visual")

        if variants != "e10s":
            variants = variants.replace("e10s", "")

        mod_test_name = f"{test}-{app}" + "-".join(sorted(extras))
        test_data = org_data.setdefault(
            platform, {}
        ).setdefault(
            app, {}
        ).setdefault(
            variants, {}
        ).setdefault(
            pl_type, {}
        ).setdefault(
            mod_test_name, {}
        )

        # Make sure we're never mixing data
        if "extra_options" in test_data:
            assert test_data["extra_options"] == set(list(extras))
        else:
            test_data["extra_options"] = set(list(extras))
        
        test_data.setdefault("values", {}).setdefault(
            entry[time_ind], []
        ).append(float(entry[val_ind]))

    if not org_data:
        possible_platforms = set([entry[platform_ind] for entry in data])
        raise Exception(
            "Could not find any requested platforms in the data. Possible choices are: "
            f"{possible_platforms}"
        )

    return org_data


def geo_mean(iterable):
    a = np.array(iterable)
    return a.prod()**(1.0/len(a))


def temporal_aggregation(times, timespan=24):
    """Aggregates times formatted like `YYYY-mm-dd HH:MM`.

    After aggregation, the result will contain lists of all
    points that were grouped together. Timespan distancing
    starts from the newest data point.
    """
    aggr_times = []
    diff = datetime.timedelta(hours=timespan)

    curr = []
    for t in sorted(times)[::-1]:

        dt = datetime.datetime.strptime(t, "%Y-%m-%d %H:%M")
        if len(curr) == 0:
            curr.append(dt)
        elif curr[0] - dt < diff:
            curr.append(dt)
        else:
            aggr_times.append([c.strftime("%Y-%m-%d %H:%M") for c in curr])
            curr = [dt]

    return aggr_times[::-1]


def summarize(data, platforms, timespan):
    org_data = organize_data(data, platforms)

    summary = {}

    for platform, apps in org_data.items():

        for app, variants in apps.items():

            for variant, pl_types in variants.items():

                for pl_type, tests in pl_types.items():
                    # Get all the push times and aggregate them
                    all_push_times = []
                    for _, info in tests.items():
                        all_push_times.extend(list(info["values"].keys()))
                    all_push_times = temporal_aggregation(list(set(all_push_times)), timespan)

                    # Get a summary value for each push time
                    summarized_vals = []
                    for times in sorted(all_push_times):

                        vals = {}
                        for time in times:
                            for test, info in tests.items():
                                if time not in info["values"]:
                                    continue
                                vals.setdefault(test, []).extend(info["values"][time])

                        vals = [np.mean(v) for _, v in vals.items()]
                        summarized_vals.append((times[-1], geo_mean(np.asarray(vals))))

                    summary.setdefault(
                        platform, {}
                    ).setdefault(
                        app, {}
                    ).setdefault(
                        variant, {}
                    )[pl_type] = {
                        "values": summarized_vals,
                    }

    return summary


def view(summary):

    for platform, apps in summary.items():

        for app, variants in apps.items():

            for variant, pl_types in variants.items():

                """
                This is a simple visualization to show the metric. It
                can be modified to anything.
                """

                plt.figure()
                figc = 1
                for pl_type, vals in pl_types.items():
                    plt.subplot(1,2,figc)
                    figc += 1

                    variant = variant if variant != "None" else "e10s"
                    plt.title(platform + f"\n{app}-{pl_type}-{variant}")

                    times = [
                        datetime.datetime.strptime(x, "%Y-%m-%d %H:%M")
                        for x, y in vals["values"]
                    ]
                    vals = [y for x, y in vals["values"]]

                    md_times = md.date2num(times)

                    ax = plt.gca()
                    xfmt = md.DateFormatter('%Y-%m-%d %H:%M:%S')
                    ax.xaxis.set_major_formatter(xfmt)
                    plt.xticks(rotation=25)

                    plt.plot(md_times, vals)

                plt.show()



def main():
    args = summary_parser().parse_args()

    # Check data path and setup output
    data_path = pathlib.Path(args.data)
    if not data_path.exists():
        raise Exception(f"The given data file doesn't exist: {args.data}")

    output_folder = pathlib.Path(args.output)
    output_file = "summary.json"

    if output_folder.exists() and output_folder.is_file():
        print(f"Deleting existing JSON file at: {output_folder}")
        output_folder.unlink()

    if not output_folder.exists():
        if pathlib.Path(output_folder.parts[-1]).suffixes:
            # A JSON file name was given
            output_file = output_folder.parts[-1]
            output_folder = pathlib.Path(*output_folder.parts[:-1])
        output_folder.mkdir(parents=True, exist_ok=True)

    # Process the data and visualize the results (after saving)
    data = open_csv_data(data_path)

    results = summarize(data, args.platforms, args.timespan)
    with pathlib.Path(output_folder, output_file).open("w") as f:
        json.dump(results, f)

    view(results)


if __name__ == "__main__":
    main()