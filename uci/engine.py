# Copyright (C) 2013-2017 Jean-Francois Romang (jromang@posteo.de)
#                         Shivkumar Shivaji ()
#                         Jürgen Précour (LocutusOfPenguin@posteo.de)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from dgt.api import Event
from dgt.util import EngineStatus
from utilities import Observable
import logging
import os
import spur
import paramiko
import chess.uci
from chess import Board
from uci.informer import Informer
import configparser


class UciEngine(object):

    """handle the uci engine communication."""

    def __init__(self, file: str, hostname=None, username=None, key_file=None, password=None, home=''):
        super(UciEngine, self).__init__()
        try:
            self.shell = None
            if hostname:
                logging.info('connecting to [%s]', hostname)
                if key_file:
                    shell = spur.SshShell(hostname=hostname, username=username, private_key_file=key_file,
                                          missing_host_key=paramiko.AutoAddPolicy())
                else:
                    shell = spur.SshShell(hostname=hostname, username=username, password=password,
                                          missing_host_key=paramiko.AutoAddPolicy())
                self.shell = shell
                self.engine = chess.uci.spur_spawn_engine(shell, [home + os.sep + file])
            else:
                self.engine = chess.uci.popen_engine(file)

            self.file = file
            if self.engine:
                handler = Informer()
                self.engine.info_handlers.append(handler)
                self.engine.uci()
            else:
                logging.error('engine executable [%s] not found', file)
            self.options = {}
            self.future = None
            self.show_best = True

            self.res = None
            self.status = EngineStatus.WAIT
            self.level_support = False

        except OSError:
            logging.exception('OS error in starting engine')
        except TypeError:
            logging.exception('engine executable not found')

    def get(self):
        return self.engine

    def option(self, name, value):
        self.options[name] = value

    def send(self):
        self.engine.setoption(self.options)

    def level(self, options: dict):
        self.options = options

    def has_levels(self):
        return self.level_support or self.has_skill_level() or self.has_limit_strength() or self.has_strength()

    def has_skill_level(self):
        return 'Skill Level' in self.engine.options

    def has_limit_strength(self):
        return 'UCI_LimitStrength' in self.engine.options

    def has_strength(self):
        return 'Strength' in self.engine.options

    def has_chess960(self):
        return 'UCI_Chess960' in self.engine.options

    def get_file(self):
        return self.file

    def get_shell(self):
        return self.shell  # shell is only "not none" if its a local engine - see __init__

    def position(self, game: Board):
        self.engine.position(game)

    def quit(self):
        return self.engine.quit()

    def terminate(self):
        return self.engine.terminate()

    def kill(self):
        return self.engine.kill()

    def uci(self):
        self.engine.uci()

    def stop(self, show_best=False):
        if self.is_waiting():
            logging.info('engine already stopped')
            return self.res
        self.show_best = show_best
        self.engine.stop()
        return self.future.result()

    def go(self, time_dict: dict):
        if not self.is_waiting():
            logging.warning('engine (still) not waiting - strange!')
        self.status = EngineStatus.THINK
        self.show_best = True
        time_dict['async_callback'] = self.callback

        Observable.fire(Event.START_SEARCH(engine_status=self.status))
        self.future = self.engine.go(**time_dict)
        return self.future

    def ponder(self):
        if not self.is_waiting():
            logging.warning('engine (still) not waiting - strange!')
        self.status = EngineStatus.PONDER
        self.show_best = False

        Observable.fire(Event.START_SEARCH(engine_status=self.status))
        self.future = self.engine.go(ponder=True, infinite=True, async_callback=self.callback)
        return self.future

    def callback(self, command):
        self.res = command.result()

        Observable.fire(Event.STOP_SEARCH(engine_status=self.status))
        if self.show_best:
            Observable.fire(Event.BEST_MOVE(move=self.res.bestmove, ponder=self.res.ponder, inbook=False))
        else:
            logging.debug('event best_move not fired')
        self.status = EngineStatus.WAIT

    def is_thinking(self):
        return self.status == EngineStatus.THINK

    def is_pondering(self):
        return self.status == EngineStatus.PONDER

    def is_waiting(self):
        return self.status == EngineStatus.WAIT

    def startup(self, options: dict, show=True):
        parser = configparser.ConfigParser()
        parser.optionxform = str
        if not options and parser.read(self.get_file() + '.uci'):
            options = dict(parser[parser.sections().pop()])
        self.level_support = bool(options)
        if parser.read(os.path.dirname(self.get_file()) + os.sep + 'engines.uci'):
            pc_opts = dict(parser[parser.sections().pop()])
            pc_opts.update(options)
            options = pc_opts

        logging.debug('setting engine with options %s', options)
        self.level(options)
        self.send()
        if show:
            logging.debug('Loaded engine [%s]', self.get().name)
            logging.debug('Supported options [%s]', self.get().options)
