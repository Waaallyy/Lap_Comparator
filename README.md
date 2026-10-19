# Assetto Corsa Lap Comparator

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-150458?style=for-the-badge&logo=pandas&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-013243?style=for-the-badge&logo=numpy&logoColor=white)
![Matplotlib](https://img.shields.io/badge/Matplotlib-11557C?style=for-the-badge&logo=python&logoColor=white)

An interactive tool to compare my own laps in Assetto Corsa, built from telemetry recorded in game.

Load two or more laps, and see exactly where time is gained or lost: cumulative time delta, speed, brake, throttle and RPM, side by side with a track map colored by time lost every 100 m.

## Demo

![Demo](gif_projet_asseto.gif)

## Features

- **Lap comparison**: the first file is the reference, every other lap is compared against it on the same distance axis.
- **Time delta**: cumulative gap to the reference along the lap (positive = slower).
- **Telemetry channels**: speed, brake, throttle and RPM, one panel each. Channels missing from a file are skipped automatically.
- **Track map**: the circuit drawn from car position data, colored by where time is lost (red) or gained (blue).
- **Linked cursor**: hovering any chart moves a cursor across every panel and a marker on the track map, showing each lap's values and elapsed time at that exact point.
- **Large map mode**: an optional separate window with a full-size track map.

## Installation

You need Python 3. Then, from the project folder:

```bash
python -m pip install numpy pandas matplotlib
```

## Usage

Compare two laps (the first one is the reference):

```bash
python data_analys.py reference_lap.csv other_lap.csv
```

Compare several laps at once:

```bash
python data_analys.py reference_lap.csv lap_2.csv lap_3.csv
```

Also open a large track map in a separate window:

```bash
python data_analys.py reference_lap.csv other_lap.csv --carte
```

The terminal prints the lap times and the final gap of each lap to the reference, then the charts open.

## CSV format

Each lap is a CSV file. Optional metadata lines starting with `#` can appear at the top:

```
# circuit=spa
# temps=1_48_595
Temps(s),Distance(m),Vitesse(kmh),Brake(%),Throttle(%),RPM,PosX,PosZ
0.000,0.0,218.4,0,100,17950,-412.3,118.7
...
```

| Column | Required | Used for |
|---|---|---|
| `Temps(s)` | Yes | Elapsed time, used to compute the delta |
| `Distance(m)` | Yes | Common axis for all laps |
| `Vitesse(kmh)`, `Brake(%)`, `Throttle(%)`, `RPM` | No | Telemetry panels |
| `PosX`, `PosZ` | No | Track map |

Metadata: `circuit` sets the chart title, `temps` (format `M_SS_mmm`) sets the official lap time. Without it, the last time sample is used.

## How it works

- **Common distance grid**: laps never have samples at the same positions, so each lap is resampled onto a shared grid of 3,000 points covering the distance all laps have in common.
- **Time delta**: at each point of the grid, the reference lap's elapsed time is subtracted from the other lap's.
- **Where time is lost**: the cumulative delta stays high after a missed corner until the end of the lap, so it does not show *where* time was lost. The track map uses its smoothed slope instead, in seconds per 100 m.
- **Clean data**: samples where distance goes backwards are removed, since interpolation needs a strictly increasing axis.
- **Smooth hovering**: the cursor uses Matplotlib blitting. The full figure is drawn once, and each mouse move only redraws the few moving elements, which keeps hovering fluid even with thousands of points.

## Tech stack

- **Python**: core language
- **Pandas**: CSV loading, cleaning and smoothing
- **NumPy**: interpolation and delta computation
- **Matplotlib**: charts, track map and interactive cursor

> **Note:** the chart labels are in French.

## License

This project is for educational and portfolio purposes. Assetto Corsa is a trademark of Kunos Simulazioni.
