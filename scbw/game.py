import logging
import signal
import time
from argparse import Namespace
from typing import List, Optional, Callable

from scbw.bot_factory import retrieve_bots
from scbw.bot_storage import LocalBotStorage, SscaitBotStorage
from scbw.docker_utils import (
    dockermachine_ip, launch_game,
    remove_game_containers
)
from scbw.error import GameException, RealtimeOutedException
from scbw.game_type import GameType
from scbw.player import HumanPlayer, BotPlayer
from scbw.plot import RealtimeFramePlotter
from scbw.result import GameResult
from scbw.vnc import check_vnc_exists


logger = logging.getLogger(__name__)


class GameArgs(Namespace):
    bots: List[str]
    human: bool
    map: str
    headless: bool
    game_name: str
    game_type: str
    game_speed: int
    hide_names: bool
    timeout: int
    bot_dir: str
    log_dir: str
    map_dir: str
    bwapi_data_bwta_dir: str
    bwapi_data_bwta2_dir: str
    vnc_base_port: int
    vnc_host: str
    capture_movement: bool
    launch_multiplayer: bool
    show_all: bool
    allow_input: bool
    plot_realtime: bool
    read_overwrite: bool
    docker_image: str
    opt: str


def run_game(
    args: GameArgs,
    wait_callback: Optional[Callable] = None
) -> Optional[GameResult]:
    # Check all startup requirements
    if not args.headless:
        check_vnc_exists()
    if args.human and args.headless:
        raise GameException("Cannot use human play in headless mode")
    if args.headless and args.show_all:
        raise GameException("Cannot show all screens in headless mode")

    # Each game is prefixed with "GAME_"
    # this is needed for game filtering in docker ps
    game_name = "GAME_" + args.game_name

    # Prepare players
    players = []
    if args.human:
        players.append(HumanPlayer())
    if args.bots is None:
        args.bots = []
    bot_storages = (
        LocalBotStorage(args.bot_dir),
        SscaitBotStorage(args.bot_dir)
    )
    players += retrieve_bots(args.bots, bot_storages)

    is_1v1_game = len(players) == 2

    opts = [] if not args.opt else args.opt.split(" ")

    if args.vnc_host == "":
        args.vnc_host = dockermachine_ip() or "localhost"
        logger.debug(f"Using vnc host '{args.vnc_host}'")

    # make sure we always have a sleeping wait callback!
    if wait_callback is None:
        wait_callback = lambda: time.sleep(3)

    if args.plot_realtime:
        plot_realtime = RealtimeFramePlotter(args.log_dir, game_name, players)

        def _wait_callback():
            plot_realtime.redraw()
            wait_callback()
    else:
        _wait_callback = wait_callback

    # Prepare game launching
    launch_params = dict(
        # game settings
        headless=args.headless,
        game_name=game_name,
        map_name=args.map,
        game_type=GameType(args.game_type),
        game_speed=args.game_speed,
        timeout=args.timeout,
        hide_names=args.hide_names,
        drop_players=any(isinstance(player, BotPlayer)
                         and player.meta.javaDebugPort is not None
                         for player in players),
        allow_input=args.allow_input,
        launch_multiplayer=args.launch_multiplayer,

        # mount dirs
        log_dir=args.log_dir,
        bot_dir=args.bot_dir,
        map_dir=args.map_dir,
        bwapi_data_bwta_dir=args.bwapi_data_bwta_dir,
        bwapi_data_bwta2_dir=args.bwapi_data_bwta2_dir,

        # vnc
        vnc_base_port=args.vnc_base_port,
        vnc_host=args.vnc_host,
        capture_movement=args.capture_movement,

        # docker
        docker_image=args.docker_image,
        docker_opts=opts,
    )

    time_start = time.time()
    is_realtime_outed = False
    try:
        launch_game(
            players, launch_params, args.show_all,
            args.read_overwrite, _wait_callback
        )
    except RealtimeOutedException:
        is_realtime_outed = True

    except KeyboardInterrupt:
        logger.warning("Caught interrupt, shutting down containers")
        logger.warning("This can take a moment, please wait.")

        # prevent another throw of KeyboardInterrupt exception
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        remove_game_containers(game_name)
        logger.info(f"Game cancelled.")
        raise

    if args.plot_realtime:
        plot_realtime.save(f"{args.log_dir}/{game_name}_frameplot.png")

    if is_1v1_game:
        game_time = time.time() - time_start
        return GameResult(
            game_name, players, game_time,
            # game error states
            is_realtime_outed,
            # dirs with results
            args.map_dir, args.log_dir
        )

    return None
