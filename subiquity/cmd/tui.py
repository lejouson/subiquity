#!/usr/bin/env python3
# Copyright 2015 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import asyncio
import fcntl
import logging
import os
import subprocess
import sys

from subiquitycore.log import setup_logger

from .common import LOGDIR, setup_environment
from .server import make_server_args_parser


def make_client_args_parser():
    parser = argparse.ArgumentParser(
        description="Subiquity - Ubiquity for Servers", prog="subiquity"
    )
    try:
        ascii_default = os.ttyname(0) == "/dev/ttysclp0"
    except OSError:
        ascii_default = False
    parser.set_defaults(ascii=ascii_default)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="menu-only, do not call installer function",
    )
    parser.add_argument("--socket")
    parser.add_argument(
        "--serial",
        action="store_true",
        dest="run_on_serial",
        help="Run the installer over serial console.",
    )
    parser.add_argument(
        "--ssh", action="store_true", dest="ssh", help="Print ssh login details"
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        dest="ascii",
        help="Run the installer in ascii mode.",
    )
    parser.add_argument(
        "--unicode",
        action="store_false",
        dest="ascii",
        help="Run the installer in unicode mode.",
    )
    parser.add_argument("--screens", action="append", dest="screens", default=[])
    parser.add_argument("--answers")
    parser.add_argument("--server-pid")
    parser.add_argument(
        "--output-base",
        action="store",
        dest="output_base",
        default=".subiquity",
        help="in dryrun, control basedir of files",
    )
    # Only for debugging - helps to reproduce some storage v2 guided scenarios
    # issues.
    parser.add_argument(
        "--debug-sv2-guided", action="store_true", help=argparse.SUPPRESS
    )
    return parser


AUTO_ANSWERS_FILE = "/subiquity_config/answers.yaml"


def main():
    setup_environment()
    from subiquity.client.client import SubiquityClient

    parser = make_client_args_parser()
    args = sys.argv[1:]
    if "--dry-run" in args:
        opts, unknown = parser.parse_known_args(args)
        if opts.socket is None:
            base = opts.output_base
            os.makedirs(base, exist_ok=True)
            if os.path.exists(f"{base}/run/subiquity/server-state"):
                os.unlink(f"{base}/run/subiquity/server-state")
            sock_path = base + "/socket"
            opts.socket = sock_path
            server_args = ["--dry-run", "--socket=" + sock_path] + unknown
            server_args.extend(("--output-base", base))
            server_parser = make_server_args_parser()
            server_parser.parse_args(server_args)  # just to check
            server_stdout = open(os.path.join(base, "server-stdout"), "w")
            server_stderr = open(os.path.join(base, "server-stderr"), "w")
            server_cmd = [sys.executable, "-m", "subiquity.cmd.server"] + server_args
            server_proc = subprocess.Popen(
                server_cmd, stdout=server_stdout, stderr=server_stderr
            )
            opts.server_pid = str(server_proc.pid)
            print("running server pid {}".format(server_proc.pid))
        elif opts.server_pid is not None:
            print("reconnecting to server pid {}".format(opts.server_pid))
        else:
            opts = parser.parse_args(args)
    else:
        opts = parser.parse_args(args)
        if opts.socket is None:
            opts.socket = "/run/subiquity/socket"
    os.makedirs(os.path.basename(opts.socket), exist_ok=True)
    logdir = LOGDIR
    if opts.dry_run:
        logdir = opts.output_base
    logfiles = setup_logger(dir=logdir, base="subiquity-client")

    logging.captureWarnings(True)
    logger = logging.getLogger("subiquity")
    version = os.environ.get("SNAP_REVISION", "unknown")
    snap = os.environ.get("SNAP", "unknown")
    logger.info(f"Starting Subiquity TUI revision {version} of snap {snap}")
    logger.info(f"Arguments passed: {sys.argv}")
    logger.debug(f"Environment: {os.environ}")

    if opts.answers is None and os.path.exists(AUTO_ANSWERS_FILE):
        logger.debug("Autoloading answers from %s", AUTO_ANSWERS_FILE)
        opts.answers = AUTO_ANSWERS_FILE

    if opts.answers:
        opts.answers = open(opts.answers)
        try:
            fcntl.flock(opts.answers, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.exception("Failed to lock auto answers file, proceding without it.")
            opts.answers.close()
            opts.answers = None

    async def run_with_loop():
        subiquity_interface = SubiquityClient(opts)
        subiquity_interface.note_file_for_apport(
            "InstallerClientLog", logfiles["debug"]
        )
        subiquity_interface.note_file_for_apport(
            "InstallerClientLogInfo", logfiles["info"]
        )
        await subiquity_interface.run()

    asyncio.run(run_with_loop())


if __name__ == "__main__":
    sys.exit(main())
