import ac
import acsys
import os
import csv

# FIXED column order. Never use lap_data[0].keys(): Assetto Corsa ships
# Python 3.3, where dicts are unordered and hash randomization is enabled.
# The order would change from one launch to the next.
# Column names are kept as is: lap_comparator.py reads them by name.
COLUMNS = [
    "Temps(s)",
    "NSP",
    "Distance(m)",
    "PosX",
    "PosZ",
    "Vitesse(kmh)",
    "RPM",
    "Throttle(%)",
    "Brake(%)",
]

lap_data = []
last_known_lap = 0
track_length = 0.0
track_name = "unknown"


def acMain(ac_version):
    """Called once when the app starts."""
    global last_known_lap, track_length, track_name

    car_id = 0
    last_known_lap = ac.getCarState(car_id, acsys.CS.LapCount)

    track_length = ac.getTrackLength(car_id)
    track_name = ac.getTrackName(car_id)
    config = ac.getTrackConfiguration(car_id)
    if config:
        track_name = track_name + "-" + config

    ac.log("TelemetryExporter: track={} length={} m".format(
        track_name, track_length))
    return "TelemetryExporter"


def acUpdate(delta_t):
    """Called every frame."""
    global lap_data, last_known_lap, track_length

    car_id = 0
    current_lap = ac.getCarState(car_id, acsys.CS.LapCount)

    # 0. Session restarted: AC's lap counter goes back to 0.
    #    Without this reset, no lap would be exported until the counter
    #    exceeds its old value, and the NSP filter would block recording
    #    because of the previous session's data.
    if current_lap < last_known_lap:
        lap_data = []
        last_known_lap = current_lap

    # 1. Detect the line crossing BEFORE recording.
    #    Otherwise the first frame of the new lap (NSP ~ 0) lands at the end
    #    of the previous file and breaks the monotonicity of the Distance column.
    if current_lap > last_known_lap:
        last_lap_ms = ac.getCarState(car_id, acsys.CS.LastLap)
        export_csv(last_known_lap, format_time(last_lap_ms))
        lap_data = []
        last_known_lap = current_lap

    # 2. Record the current frame
    if not ac.isConnected(car_id):
        return

    # Safety net: the track length is sometimes not available in acMain
    if track_length <= 0:
        track_length = ac.getTrackLength(car_id)
        if track_length <= 0:
            return

    nsp = ac.getCarState(car_id, acsys.CS.NormalizedSplinePosition)

    # Only record if the car has actually moved forward.
    # Guarantees a strictly increasing Distance column, which the
    # interpolation in the analysis requires.
    if lap_data and nsp <= lap_data[-1]["NSP"]:
        return

    position = ac.getCarState(car_id, acsys.CS.WorldPosition)
    try:
        pos_x = round(position[0], 2)
        pos_z = round(position[2], 2)  # index 1 = altitude, index 2 = horizontal axis
    except Exception:
        pos_x = 0.0
        pos_z = 0.0

    frame = {
        "Temps(s)": round(ac.getCarState(car_id, acsys.CS.LapTime) / 1000.0, 3),
        "NSP": round(nsp, 6),
        "Distance(m)": round(nsp * track_length, 2),
        "PosX": pos_x,
        "PosZ": pos_z,
        "Vitesse(kmh)": round(ac.getCarState(car_id, acsys.CS.SpeedKMH), 1),
        "RPM": int(ac.getCarState(car_id, acsys.CS.RPM)),
        "Throttle(%)": round(ac.getCarState(car_id, acsys.CS.Gas) * 100, 1),
        "Brake(%)": round(ac.getCarState(car_id, acsys.CS.Brake) * 100, 1),
    }
    lap_data.append(frame)


def format_time(ms):
    """Converts milliseconds to MM_SS_mmm (safe for file names)."""
    minutes = int(ms / 60000)
    seconds = int((ms % 60000) / 1000)
    milliseconds = int(ms % 1000)
    return "{:02d}_{:02d}_{:03d}".format(minutes, seconds, milliseconds)


def clean_name(text):
    """Removes characters that are not allowed in a file name."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in text)


def export_csv(lap_number, formatted_time):
    """Writes a CSV to Documents/AC_Telemetry_Exports."""
    global lap_data

    if not lap_data:
        return

    # An out lap from the pits is incomplete: skip it.
    if lap_data[0]["NSP"] > 0.1:
        ac.log("TelemetryExporter: incomplete lap skipped")
        return

    folder = os.path.join(os.path.expanduser("~"), "Documents", "AC_Telemetry_Exports")
    if not os.path.exists(folder):
        os.makedirs(folder)

    file_name = "Lap_{}_{}_{}.csv".format(
        lap_number, formatted_time, clean_name(track_name))
    full_path = os.path.join(folder, file_name)

    try:
        with open(full_path, "w", newline="") as f:
            # Metadata as comment lines, ignored by pandas
            # through read_csv(..., comment="#").
            # Keys are kept as is: lap_comparator.py reads "circuit" and "temps".
            f.write("# circuit={}\n".format(track_name))
            f.write("# longueur_m={}\n".format(round(track_length, 1)))
            f.write("# tour={}\n".format(lap_number))
            f.write("# temps={}\n".format(formatted_time))

            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(lap_data)

        ac.log("TelemetryExporter: file saved -> " + full_path)
    except Exception as e:
        ac.log("TelemetryExporter error: " + str(e))
