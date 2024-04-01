from argparse import ArgumentParser

import rpyc  # type: ignore

from k3ng import K3NGService

if __name__ == "__main__":
    parser = ArgumentParser(
        prog="rpc_telegraf",
        description="Polls a K3NG rotator over RPC for basic telemetry",
    )
    parser.add_argument(
        "rpc_port",
        type=int,
        nargs="?",
        default=K3NGService.DEFAULT_PORT,
        help="Port of RPC server on localhost",
    )

    args = parser.parse_args()

    rot = rpyc.connect("localhost", args.rpc_port).root.K3NG

    az = rot.get_azimuth()
    el = rot.get_elevation()
    state = rot.get_tracking_status()

    # Format for Telegraf usage
    print(f"rotator_azimuth {az}")
    print(f"rotator_elevation {el}")
    print(
        f"rotator_tracking satname={state.satname},sat_state={state.sat_state.name},"
        f"next_event={state.next_event.name},next_event_mins={state.next_event_mins}"
        f" {int(state.is_tracking)}"
    )
