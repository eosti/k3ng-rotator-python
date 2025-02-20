import time
from argparse import ArgumentParser

from k3ng import RotctlK3NG

if __name__ == "__main__":
    parser = ArgumentParser(
        prog="rotctl_telegraf",
        description="Polls a local instance of rotctl for telemetry",
    )
    parser.add_argument(
        "rotctl_port",
        type=int,
        nargs="?",
        default=4533,
        help="Port of rotctl server on localhost",
    )

    args = parser.parse_args()

    rot = RotctlK3NG("localhost", args.rotctl_port)

    az = rot.azimuth
    el = rot.elevation
    state = rot.get_tracking_status()

    # Format for Telegraf usage
    # Measurement
    measurement = "rotator,"
    # Tags
    measurement += f"satname={state.satname},sat_state={state.sat_state.name},"
    measurement += f"next_event={state.next_event.name} "
    # Fields
    measurement += f"next_event_mins={state.next_event_mins},"
    measurement += f"azimuth={az},"
    measurement += f"elevation={el},"
    measurement += f"is_tracking={int(state.is_tracking)} "
    # Timestamp
    measurement += str(time.time_ns())

    print(measurement)
