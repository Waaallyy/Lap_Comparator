#!/usr/bin/env python3
"""
Assetto Corsa telemetry lap comparison.

Usage:
    python lap_comparator.py reference_lap.csv lap_2.csv [lap_3.csv ...]
    python lap_comparator.py reference_lap.csv lap_2.csv --map

The FIRST file is the reference, the following ones are compared to it.
Hovering a chart moves a cursor across ALL panels and a marker on the
track map.
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

# CSV column names, as written by TelemetryExporter.
COL_TIME = "Temps(s)"
COL_DIST = "Distance(m)"
COL_X = "PosX"
COL_Z = "PosZ"

# Plotted channels, one panel each. Those missing from the CSV are skipped.
# (column, axis label, forced vertical limits or None)
CHANNELS = [
    ("Vitesse(kmh)", "Speed\n(km/h)", None),
    ("Brake(%)", "Brake\n(%)", (-5, 105)),
    ("Throttle(%)", "Throttle\n(%)", (-5, 105)),
    ("RPM", "RPM", None),
]

# One color per lap. All channels of a given lap share its color.
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]

GRID_POINTS = 3000
SMOOTHING_WINDOW = 41


# ---------------------------------------------------------------- loading

def read_metadata(path):
    """Reads the '# key=value' lines at the top of the file."""
    meta = {}
    with open(path, "r") as f:
        for line in f:
            if not line.startswith("#"):
                break
            if "=" in line:
                key, value = line[1:].split("=", 1)
                meta[key.strip()] = value.strip()
    return meta


def increasing_mask(values):
    """Mask keeping only strictly increasing values.

    np.interp requires an increasing x axis. A plain diff() is not enough:
    it compares to the previous row, not to the last kept point.
    """
    maxima = np.maximum.accumulate(values)
    return np.concatenate(([True], values[1:] > maxima[:-1]))


def load(path):
    """Reads a lap CSV and returns a ready-to-use dictionary."""
    if not os.path.exists(path):
        raise FileNotFoundError("File not found: {}".format(path))

    meta = read_metadata(path)
    df = pd.read_csv(path, comment="#")

    for column in [COL_TIME, COL_DIST]:
        if column not in df.columns:
            raise ValueError(
                "{}: missing column '{}'.\nThis file comes from an old "
                "version of the exporter, the laps must be recorded again."
                .format(os.path.basename(path), column))

    for column in df.columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=[COL_TIME, COL_DIST])
    df = df[increasing_mask(df[COL_DIST].to_numpy())]

    if len(df) < 10:
        raise ValueError("{}: not enough usable data points".format(path))

    return {
        "name": os.path.splitext(os.path.basename(path))[0],
        "meta": meta,
        "dist": df[COL_DIST].to_numpy(),
        "time": df[COL_TIME].to_numpy(),
        "channels": {col: df[col].to_numpy()
                     for col, _, _ in CHANNELS if col in df.columns},
        "x": df[COL_X].to_numpy() if COL_X in df.columns else None,
        "z": df[COL_Z].to_numpy() if COL_Z in df.columns else None,
    }


def format_time(seconds):
    """Seconds -> M:SS.mmm"""
    if not np.isfinite(seconds):
        return "--"
    minutes = int(seconds // 60)
    return "{:d}:{:06.3f}".format(minutes, seconds - 60 * minutes)


def lap_time(run):
    """Lap time: the metadata value if present, otherwise the last sample."""
    raw = run["meta"].get("temps")
    if raw:
        try:
            m, s, ms = raw.split("_")
            return int(m) * 60 + int(s) + int(ms) / 1000.0
        except ValueError:
            pass
    return float(run["time"][-1])


# ---------------------------------------------------------------- computation

def build_grid(runs):
    """Distance grid covered by ALL laps."""
    start = max(r["dist"][0] for r in runs)
    end = min(r["dist"][-1] for r in runs)
    if end <= start:
        raise ValueError("The laps do not overlap on any section.")
    return np.linspace(start, end, GRID_POINTS)


def compute_delta(reference, run, grid):
    """Cumulative time gap at equal distance. Positive = run is slower."""
    t_ref = np.interp(grid, reference["dist"], reference["time"])
    t_run = np.interp(grid, run["dist"], run["time"])
    return t_run - t_ref


def loss_rate(grid, delta):
    """Smoothed derivative of the delta, in seconds per 100 m.

    The cumulative delta does not tell WHERE time is lost: after a missed
    corner, it stays high until the end of the lap. Its slope is what
    locates the loss.
    """
    smoothed = pd.Series(delta).rolling(
        SMOOTHING_WINDOW, center=True, min_periods=1).mean().to_numpy()
    return np.gradient(smoothed, grid) * 100.0


# ---------------------------------------------------------------- cursor

class LinkedCursor:
    """Single cursor shared by all panels and the track map.

    Uses blitting: the full figure is rendered once and cached, then each
    mouse move only redraws the few moving artists. Without it, redrawing
    thousands of points on every pixel moved would make hovering unusable.
    """

    def __init__(self, fig, channel_axes, map_ax, runs, channels,
                 grid, deltas, delta_ax):
        self.fig = fig
        self.channel_axes = channel_axes
        self.data_axes = channel_axes[-len(channels):]
        self.map_ax = map_ax
        self.delta_ax = delta_ax
        self.runs = runs
        self.channels = channels
        self.grid = grid
        self.deltas = deltas
        self.background = None
        self.active = False

        self.moving = []        # every artist redrawn on hover
        self.lines = []         # one vertical line per panel
        self.markers = {}       # (axis, run index) -> marker
        self.labels = {}        # (axis, run index) -> displayed value
        self.map_markers = []   # marker on the track, one per run
        self.map_label = None

        for axis in channel_axes:
            line = axis.axvline(0, color="#52514e", linewidth=0.8,
                                animated=True, visible=False)
            self.lines.append(line)
            self.moving.append(line)

            for index in range(len(runs)):
                color = COLORS[index % len(COLORS)]
                marker, = axis.plot([], [], "o", markersize=5, color=color,
                                    markeredgecolor="white", markeredgewidth=0.8,
                                    animated=True, visible=False)
                label = axis.text(0.997, 0.94 - 0.17 * index, "",
                                  transform=axis.transAxes, ha="right", va="top",
                                  fontsize=8.5, color=color, animated=True,
                                  visible=False)
                self.markers[(axis, index)] = marker
                self.labels[(axis, index)] = label
                self.moving.extend([marker, label])

        if map_ax is not None:
            for index, run in enumerate(runs):
                if run["x"] is None:
                    self.map_markers.append(None)
                    continue
                color = COLORS[index % len(COLORS)]
                marker, = map_ax.plot([], [], "o", markersize=9, color=color,
                                      markeredgecolor="white",
                                      markeredgewidth=1.4, zorder=5,
                                      animated=True, visible=False)
                self.map_markers.append(marker)
                self.moving.append(marker)
            self.map_label = map_ax.text(
                0.02, 0.99, "", transform=map_ax.transAxes, ha="left",
                va="top", fontsize=9, animated=True, visible=False)
            self.moving.append(self.map_label)

        fig.canvas.mpl_connect("draw_event", self._on_draw)
        fig.canvas.mpl_connect("motion_notify_event", self._on_move)
        fig.canvas.mpl_connect("axes_leave_event", self._on_leave)

    def _on_draw(self, _event):
        """Caches the background after each full render (zoom, resize)."""
        self.background = self.fig.canvas.copy_from_bbox(self.fig.bbox)

    def _on_leave(self, _event=None):
        if not self.active:
            return
        self.active = False
        for artist in self.moving:
            artist.set_visible(False)
        if self.background is not None:
            self.fig.canvas.restore_region(self.background)
            self.fig.canvas.blit(self.fig.bbox)

    def _on_move(self, event):
        if event.inaxes not in self.channel_axes or event.xdata is None:
            self._on_leave()
            return
        if self.background is None:
            return

        self.active = True
        for artist in self.moving:
            artist.set_visible(True)

        mouse_distance = event.xdata
        for line in self.lines:
            line.set_xdata([mouse_distance, mouse_distance])

        # Channel panels
        for axis, (column, _, _) in zip(self.data_axes, self.channels):
            for index, run in enumerate(self.runs):
                distance = np.clip(mouse_distance,
                                   run["dist"][0], run["dist"][-1])
                value = np.interp(distance, run["dist"], run["channels"][column])
                self.markers[(axis, index)].set_data([distance], [value])
                self.labels[(axis, index)].set_text("{:.0f}".format(value))

        # Delta panel: nothing to show for the reference itself
        if self.delta_ax is not None:
            for index in range(len(self.runs)):
                marker = self.markers[(self.delta_ax, index)]
                label = self.labels[(self.delta_ax, index)]
                if index == 0 or self.deltas[index] is None:
                    marker.set_visible(False)
                    label.set_visible(False)
                    continue
                distance = np.clip(mouse_distance,
                                   self.grid[0], self.grid[-1])
                value = np.interp(distance, self.grid, self.deltas[index])
                marker.set_data([distance], [value])
                label.set_text("{:+.3f} s".format(value))

        # Map: one marker per lap, at the same distance covered
        if self.map_ax is not None and self.map_label is not None:
            lines = ["{:.0f} m".format(mouse_distance)]
            for index, run in enumerate(self.runs):
                distance = np.clip(mouse_distance,
                                   run["dist"][0], run["dist"][-1])
                if self.map_markers[index] is not None:
                    self.map_markers[index].set_data(
                        [np.interp(distance, run["dist"], run["x"])],
                        [np.interp(distance, run["dist"], run["z"])])
                lines.append(format_time(
                    np.interp(distance, run["dist"], run["time"])))
            self.map_label.set_text("\n".join(lines))

        self.fig.canvas.restore_region(self.background)
        for artist in self.moving:
            if artist.get_visible():
                artist.axes.draw_artist(artist)
        self.fig.canvas.blit(self.fig.bbox)


# ---------------------------------------------------------------- plotting

def draw_track(ax, runs, grid, deltas):
    """Track outline, colored by loss rate when laps are compared."""
    reference = runs[0]
    if reference["x"] is None:
        ax.text(0.5, 0.5, "No position data\nin the CSV", ha="center",
                va="center", transform=ax.transAxes, color="#888780")
        ax.axis("off")
        return None

    points = np.column_stack([reference["x"], reference["z"]]).reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)

    if len(runs) > 1 and deltas[1] is not None:
        rate = np.interp(reference["dist"], grid,
                         loss_rate(grid, deltas[1]))
        amplitude = max(float(np.nanpercentile(np.abs(rate), 98)), 1e-6)
        collection = LineCollection(segments, cmap="coolwarm", linewidths=3.5,
                                    norm=plt.Normalize(-amplitude, amplitude))
        collection.set_array(rate[:-1])
    else:
        collection = LineCollection(segments, colors="#b4b2a9", linewidths=3.5)

    ax.add_collection(collection)
    ax.autoscale()
    ax.set_aspect("equal")
    ax.axis("off")
    ax.margins(0.12)
    return collection


def plot(runs, grid, deltas, channels):
    comparison = len(runs) > 1
    row_count = (1 if comparison else 0) + len(channels)
    heights = ([0.85] if comparison else []) + [1.0] * len(channels)

    fig = plt.figure(figsize=(15, min(10.5, 2.6 + 1.5 * row_count)))
    gs = fig.add_gridspec(row_count, 2, width_ratios=[2.6, 1.4],
                          height_ratios=heights, hspace=0.12, wspace=0.08,
                          left=0.07, right=0.99, top=0.89, bottom=0.07)

    channel_axes = []
    delta_ax = None
    shared_ax = None

    # --- delta panel, at the top
    if comparison:
        delta_ax = fig.add_subplot(gs[0, 0])
        shared_ax = delta_ax
        delta_ax.axhline(0, color="#888780", linewidth=1)
        for index in range(1, len(runs)):
            color = COLORS[index % len(COLORS)]
            delta_ax.plot(grid, deltas[index], color=color, linewidth=1.5)
            delta_ax.fill_between(grid, 0, deltas[index],
                                  color=color, alpha=0.12)
        delta_ax.set_ylabel("Delta\n(s)")
        delta_ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.5)
        delta_ax.tick_params(labelbottom=False)
        channel_axes.append(delta_ax)

    # --- one panel per channel
    for rank, (column, axis_label, limits) in enumerate(channels):
        row = rank + (1 if comparison else 0)
        axis = fig.add_subplot(gs[row, 0], sharex=shared_ax)
        if shared_ax is None:
            shared_ax = axis

        for index, run in enumerate(runs):
            color = COLORS[index % len(COLORS)]
            legend_label = "{}  ({})".format(
                run["name"], format_time(lap_time(run)))
            axis.plot(run["dist"], run["channels"][column], color=color,
                      linewidth=1.5, label=legend_label if rank == 0 else None)

        axis.set_ylabel(axis_label)
        if limits:
            axis.set_ylim(*limits)
        axis.grid(True, which="major", linestyle="--", linewidth=0.6, alpha=0.5)
        axis.grid(True, which="minor", linestyle=":", linewidth=0.4, alpha=0.4)
        axis.minorticks_on()
        if rank < len(channels) - 1:
            axis.tick_params(labelbottom=False)
        else:
            axis.set_xlabel("Distance covered (m)")
        channel_axes.append(axis)

    # --- track map, right column over the full height
    map_ax = fig.add_subplot(gs[:, 1])
    collection = draw_track(map_ax, runs, grid, deltas)
    if collection is not None and collection.get_array() is not None:
        # Inset inside the map: attached to the axis, it would end up at
        # the very bottom of the column, far from the track.
        cax = map_ax.inset_axes([0.10, 0.04, 0.80, 0.022])
        colorbar = fig.colorbar(collection, cax=cax, orientation="horizontal")
        colorbar.set_label("seconds lost per 100 m", fontsize=8)
        colorbar.ax.tick_params(labelsize=7)

    handles, legend_labels = channel_axes[-len(channels)].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center",
               bbox_to_anchor=(0.5, 0.96), ncol=min(len(runs), 4),
               frameon=False, fontsize=9)

    track = runs[0]["meta"].get("circuit", "unknown track")
    fig.suptitle("Lap comparison - {}".format(track), y=0.995)

    # Reference kept on the figure: without it, the garbage collector
    # destroys the cursor and hovering stops responding.
    fig._cursor = LinkedCursor(fig, channel_axes, map_ax, runs, channels,
                               grid, deltas, delta_ax)
    return fig


def plot_large_map(runs, grid, deltas):
    """Full-size track map, in a separate window."""
    fig, ax = plt.subplots(figsize=(9, 9))
    collection = draw_track(ax, runs, grid, deltas)
    if collection is not None and collection.get_array() is not None:
        fig.colorbar(collection, ax=ax, shrink=0.7,
                     label="seconds lost per 100 m")
    ax.set_title("Red = losing time, blue = gaining time", fontsize=10, pad=16)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(
        description="Compares Assetto Corsa telemetry laps.")
    parser.add_argument("files", nargs="+",
                        help="CSV files to compare, the first one is the reference")
    parser.add_argument("--map", action="store_true",
                        help="also opens a large track map")
    arguments = parser.parse_args()

    try:
        runs = [load(path) for path in arguments.files]
    except (FileNotFoundError, ValueError) as error:
        print("Error: {}".format(error))
        sys.exit(1)

    channels = [c for c in CHANNELS if all(c[0] in r["channels"] for r in runs)]
    if not channels:
        print("Error: no channel shared by all files.")
        sys.exit(1)
    print("Plotted channels: {}".format(", ".join(c[0] for c in channels)))

    grid = None
    deltas = [None] * len(runs)
    if len(runs) > 1:
        grid = build_grid(runs)
        print("Reference: {}  {}".format(
            runs[0]["name"], format_time(lap_time(runs[0]))))
        for index in range(1, len(runs)):
            deltas[index] = compute_delta(runs[0], runs[index], grid)
            print("  {}  {}  ({:+.3f} s)".format(
                runs[index]["name"],
                format_time(lap_time(runs[index])),
                deltas[index][-1]))
    else:
        print("Single file: no comparison possible.")

    plot(runs, grid, deltas, channels)

    if arguments.map:
        plot_large_map(runs, grid, deltas)

    plt.show()


if __name__ == "__main__":
    main()
