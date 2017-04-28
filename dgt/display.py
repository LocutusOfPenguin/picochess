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

import chess
from math import ceil

import logging
import copy
import queue
from utilities import DisplayMsg, Observable, switch, DispatchDgt
from dgt.translate import DgtTranslate
from dgt.menu import DgtMenu
from dgt.util import ClockSide, ClockIcons, BeepLevel, Mode, GameResult, TimeMode
from dgt.api import Dgt, Event, MessageApi

from timecontrol import TimeControl
from engine import get_installed_engines
import threading
from configobj import ConfigObj


class DgtDisplay(DisplayMsg, threading.Thread):

    """Dispatcher for Messages towards DGT hardware or back to the event system (picochess)."""

    def __init__(self, dgttranslate: DgtTranslate, dgtmenu: DgtMenu, time_control: TimeControl):
        super(DgtDisplay, self).__init__()
        self.dgttranslate = dgttranslate
        self.dgtmenu = dgtmenu
        self.time_control = time_control

        self.engine_finished = False
        self.drawresign_fen = None
        self.show_move_or_value = 0
        self.leds_are_on = False

        self.play_move = self.hint_move = self.last_move = chess.Move.null()
        self.play_fen = self.hint_fen = self.last_fen = None
        self.play_turn = self.hint_turn = self.last_turn = None
        self.score = self.dgttranslate.text('N10_score', None)
        self.depth = None

    def _exit_menu(self):
        if self.dgtmenu.inside_menu():
            self.dgtmenu.enter_top_menu()
            if not self.dgtmenu.get_confirm():
                # DispatchDgt.fire(self.dgttranslate.text('K05_exitmenu'))
                return True
        return False

    def _power_off(self, dev='web'):
        DispatchDgt.fire(self.dgttranslate.text('Y10_goodbye'))
        self.dgtmenu.set_engine_restart(True)
        Observable.fire(Event.SHUTDOWN(dev=dev))

    def _reboot(self, dev='web'):
        DispatchDgt.fire(self.dgttranslate.text('Y10_pleasewait'))
        self.dgtmenu.set_engine_restart(True)
        Observable.fire(Event.REBOOT(dev=dev))

    def _reset_moves_and_score(self):
        self.play_move = chess.Move.null()
        self.play_fen = None
        self.play_turn = None
        self.hint_move = chess.Move.null()
        self.hint_fen = None
        self.hint_turn = None
        self.last_move = chess.Move.null()
        self.last_fen = None
        self.last_turn = None
        self.score = self.dgttranslate.text('N10_score', None)
        self.depth = None

    def _combine_depth_and_score(self):
        def _score_to_string(score_val, length):
            if length == 's':
                return '{:5.2f}'.format(int(score_val) / 100).replace('.', '')
            if length == 'm':
                return '{:7.2f}'.format(int(score_val) / 100).replace('.', '')
            if length == 'l':
                return '{:9.2f}'.format(int(score_val) / 100).replace('.', '')

        score = copy.copy(self.score)
        try:
            if int(score.s) <= -1000:
                score.s = '-999'
            if int(score.s) >= 1000:
                score.s = '999'
            score.l = '{:3d}{:s}'.format(self.depth, _score_to_string(score.l[-8:], 'l'))
            score.m = '{:2d}{:s}'.format(self.depth % 100, _score_to_string(score.m[-6:], 'm'))
            score.s = '{:2d}{:s}'.format(self.depth % 100, _score_to_string(score.s[-4:], 's'))
            score.rd = ClockIcons.DOT
        except ValueError:
            pass
        return score

    def _get_clock_side(self, turn):
        side = ClockSide.LEFT if turn == chess.WHITE else ClockSide.RIGHT
        return side

    def _inside_menu(self):
        return self.dgtmenu.inside_menu()

    def _process_button0(self, dev):
        logging.debug('({}) clock: handle button 0 press'.format(dev))
        if self._inside_menu():
            text = self.dgtmenu.up()  # button0 can exit the menu, so check
            if text:
                DispatchDgt.fire(text)
            else:
                self._exit_display()
        else:
            if self.last_move:
                side = self._get_clock_side(self.last_turn)
                text = Dgt.DISPLAY_MOVE(move=self.last_move, fen=self.last_fen, side=side, wait=False, maxtime=1,
                                        beep=self.dgttranslate.bl(BeepLevel.BUTTON), devs={'ser', 'i2c', 'web'})
            else:
                text = self.dgttranslate.text('B10_nomove')
            DispatchDgt.fire(text)
            self._exit_display()

    def _process_button1(self, dev):
        logging.debug('({}) clock: handle button 1 press'.format(dev))
        if self._inside_menu():
            DispatchDgt.fire(self.dgtmenu.left())  # button1 cant exit the menu
        else:
            text = self._combine_depth_and_score()
            text.beep = self.dgttranslate.bl(BeepLevel.BUTTON)
            # text.maxtime = 0
            DispatchDgt.fire(text)
            self._exit_display()

    def _process_button2(self, dev):
        logging.debug('({}) clock: handle button 2 press'.format(dev))
        # even button2 has no function inside the menu we need to care for an "alt-move" event
        if self.dgtmenu.get_mode() in (Mode.ANALYSIS, Mode.KIBITZ, Mode.PONDER):
            text = self.dgttranslate.text('B00_nofunction')
            DispatchDgt.fire(text)
        else:
            if self.engine_finished:
                # @todo Protect against multi entrance of Alt-move
                self.engine_finished = False  # This is not 100% ok, but for the moment better as nothing
                Observable.fire(Event.ALTERNATIVE_MOVE())
            else:
                Observable.fire(Event.PAUSE_RESUME())

    def _process_button3(self, dev):
        logging.debug('({}) clock: handle button 3 press'.format(dev))
        if self._inside_menu():
            DispatchDgt.fire(self.dgtmenu.right())  # button3 cant exit the menu
        else:
            if self.hint_move:
                side = self._get_clock_side(self.hint_turn)
                text = Dgt.DISPLAY_MOVE(move=self.hint_move, fen=self.hint_fen, side=side, wait=False, maxtime=1,
                                        beep=self.dgttranslate.bl(BeepLevel.BUTTON), devs={'ser', 'i2c', 'web'})
            else:
                text = self.dgttranslate.text('B10_nomove')
            DispatchDgt.fire(text)
            self._exit_display()

    def _process_button4(self, dev):
        logging.debug('({}) clock: handle button 4 press'.format(dev))
        text = self.dgtmenu.down()  # button4 can exit the menu, so check
        if text:
            DispatchDgt.fire(text)
        else:
            Observable.fire(Event.EXIT_MENU())

    def _process_lever(self, right_side_down, dev):
        logging.debug('({}) clock: handle lever press - right_side_down: {}'.format(dev, right_side_down))
        if not self._inside_menu():
            self.play_move = chess.Move.null()
            self.play_fen = None
            self.play_turn = None
            Observable.fire(Event.SWITCH_SIDES(engine_finished=self.engine_finished))

    def _process_button(self, message):
        button = int(message.button)
        if not self.dgtmenu.get_engine_restart():
            if button == 0:
                self._process_button0(message.dev)
            elif button == 1:
                self._process_button1(message.dev)
            elif button == 2:
                self._process_button2(message.dev)
            elif button == 3:
                self._process_button3(message.dev)
            elif button == 4:
                self._process_button4(message.dev)
            elif button == 0x11:
                self._power_off(message.dev)
            elif button == 0x40:
                self._process_lever(right_side_down=True, dev=message.dev)
            elif button == -0x40:
                self._process_lever(right_side_down=False, dev=message.dev)

    def _process_fen(self, fen, raw):
        level_map = ('rnbqkbnr/pppppppp/8/q7/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/1q6/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/2q5/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/3q4/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/4q3/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/5q2/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/6q1/8/8/PPPPPPPP/RNBQKBNR',
                     'rnbqkbnr/pppppppp/8/7q/8/8/PPPPPPPP/RNBQKBNR')

        book_map = ('rnbqkbnr/pppppppp/8/8/8/q7/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/1q6/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/2q5/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/3q4/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/4q3/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/5q2/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/6q1/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/8/7q/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/q7/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/1q6/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/2q5/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/3q4/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/4q3/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/5q2/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/6q1/8/PPPPPPPP/RNBQKBNR',
                    'rnbqkbnr/pppppppp/8/8/7q/8/PPPPPPPP/RNBQKBNR')

        engine_map = ('rnbqkbnr/pppppppp/q7/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/1q6/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/2q5/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/3q4/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/4q3/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/5q2/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/6q1/8/8/8/PPPPPPPP/RNBQKBNR',
                      'rnbqkbnr/pppppppp/7q/8/8/8/PPPPPPPP/RNBQKBNR')

        shutdown_map = ('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQQBNR',
                        'RNBQQBNR/PPPPPPPP/8/8/8/8/pppppppp/rnbkqbnr',
                        '8/8/8/8/8/8/8/3QQ3',
                        '3QQ3/8/8/8/8/8/8/8')

        reboot_map = ('rnbqqbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR',
                      'RNBKQBNR/PPPPPPPP/8/8/8/8/pppppppp/rnbqqbnr',
                      '8/8/8/8/8/8/8/3qq3',
                      '3qq3/8/8/8/8/8/8/8')

        mode_map = {'rnbqkbnr/pppppppp/8/Q7/8/8/PPPPPPPP/RNBQKBNR': Mode.NORMAL,
                    'rnbqkbnr/pppppppp/8/1Q6/8/8/PPPPPPPP/RNBQKBNR': Mode.ANALYSIS,
                    'rnbqkbnr/pppppppp/8/2Q5/8/8/PPPPPPPP/RNBQKBNR': Mode.KIBITZ,
                    'rnbqkbnr/pppppppp/8/3Q4/8/8/PPPPPPPP/RNBQKBNR': Mode.OBSERVE,
                    'rnbqkbnr/pppppppp/8/4Q3/8/8/PPPPPPPP/RNBQKBNR': Mode.REMOTE,
                    'rnbqkbnr/pppppppp/8/5Q2/8/8/PPPPPPPP/RNBQKBNR': Mode.PONDER}

        drawresign_map = {'8/8/8/3k4/4K3/8/8/8': GameResult.WIN_WHITE,
                          '8/8/8/3K4/4k3/8/8/8': GameResult.WIN_WHITE,
                          '8/8/8/4k3/3K4/8/8/8': GameResult.WIN_BLACK,
                          '8/8/8/4K3/3k4/8/8/8': GameResult.WIN_BLACK,
                          '8/8/8/3kK3/8/8/8/8': GameResult.DRAW,
                          '8/8/8/3Kk3/8/8/8/8': GameResult.DRAW,
                          '8/8/8/8/3kK3/8/8/8': GameResult.DRAW,
                          '8/8/8/8/3Kk3/8/8/8': GameResult.DRAW}

        if self.dgtmenu.get_flip_board() and raw:  # Flip the board if needed
            fen = fen[::-1]
        if fen == 'RNBKQBNR/PPPPPPPP/8/8/8/8/pppppppp/rnbkqbnr':  # Check if we have to flip the board
            logging.debug('flipping the board')
            self.dgtmenu.set_position_reverse_to_flipboard()  # set standard for setup orientation too
            fen = fen[::-1]
        logging.debug("DGT-Fen [%s]", fen)
        if fen == self.dgtmenu.get_dgt_fen():
            logging.debug('ignore same fen')
            return
        self.dgtmenu.set_dgt_fen(fen)
        self.drawresign_fen = self._drawresign()
        # Fire the appropriate event
        if fen in level_map:
            eng = self.dgtmenu.get_engine()
            level_dict = eng['level_dict']
            if level_dict:
                inc = ceil(len(level_dict) / 8)
                level = min(inc * level_map.index(fen), len(level_dict) - 1)  # type: int
                self.dgtmenu.set_engine_level(level)
                msg = sorted(level_dict)[level]
                text = self.dgttranslate.text('M10_level', msg)
                text.wait = self._exit_menu()
                logging.debug("Map-Fen: New level {}".format(msg))
                config = ConfigObj('picochess.ini')
                config['engine-level'] = msg
                config.write()
                Observable.fire(Event.LEVEL(options=level_dict[msg], level_text=text))
            else:
                logging.debug('engine doesnt support levels')
        elif fen in book_map:
            book_index = book_map.index(fen)
            try:
                book = self.dgtmenu.all_books[book_index]
                self.dgtmenu.set_book(book_index)
                logging.debug("Map-Fen: Opening book [%s]", book['file'])
                text = book['text']
                text.beep = self.dgttranslate.bl(BeepLevel.MAP)
                text.maxtime = 1
                text.wait = self._exit_menu()
                Observable.fire(Event.SET_OPENING_BOOK(book=book, book_text=text, show_ok=False))
            except IndexError:
                pass
        elif fen in engine_map:
            if self.dgtmenu.installed_engines:
                try:
                    self.dgtmenu.set_engine_index(engine_map.index(fen))
                    eng = self.dgtmenu.get_engine()
                    level_dict = eng['level_dict']
                    logging.debug("Map-Fen: Engine name [%s]", eng['name'])
                    eng_text = eng['text']
                    eng_text.beep = self.dgttranslate.bl(BeepLevel.MAP)
                    eng_text.maxtime = 1
                    eng_text.wait = self._exit_menu()
                    if level_dict:
                        len_level = len(level_dict)
                        if self.dgtmenu.get_engine_level() is None or len_level <= self.dgtmenu.get_engine_level():
                            self.dgtmenu.set_engine_level(len_level - 1)
                        msg = sorted(level_dict)[self.dgtmenu.get_engine_level()]
                        options = level_dict[msg]  # cause of "new-engine", send options lateron - now only {}
                        Observable.fire(Event.LEVEL(options={}, level_text=self.dgttranslate.text('M10_level', msg)))
                    else:
                        msg = None
                        options = {}
                    config = ConfigObj('picochess.ini')
                    config['engine-level'] = msg
                    config.write()
                    Observable.fire(Event.NEW_ENGINE(eng=eng, eng_text=eng_text, options=options, show_ok=False))
                    self.dgtmenu.set_engine_restart(True)
                except IndexError:
                    pass
            else:
                DispatchDgt.fire(self.dgttranslate.text('Y00_erroreng'))
        elif fen in mode_map:
            logging.debug("Map-Fen: Interaction mode [%s]", mode_map[fen])
            self.dgtmenu.set_mode(mode_map[fen])
            text = self.dgttranslate.text(mode_map[fen].value)
            text.beep = self.dgttranslate.bl(BeepLevel.MAP)
            text.maxtime = 1  # wait 1sec not forever
            text.wait = self._exit_menu()
            Observable.fire(Event.SET_INTERACTION_MODE(mode=mode_map[fen], mode_text=text, show_ok=False))
        elif fen in self.dgtmenu.tc_fixed_map:
            logging.debug('Map-Fen: Time control fixed')
            self.dgtmenu.set_time_mode(TimeMode.FIXED)
            self.dgtmenu.set_time_fixed(list(self.dgtmenu.tc_fixed_map.keys()).index(fen))
            text = self.dgttranslate.text('M10_tc_fixed', self.dgtmenu.tc_fixed_list[self.dgtmenu.get_time_fixed()])
            text.wait = self._exit_menu()
            timectrl = self.dgtmenu.tc_fixed_map[fen]  # type: TimeControl
            Observable.fire(Event.SET_TIME_CONTROL(tc_init=timectrl.get_parameters(), time_text=text, show_ok=False))
        elif fen in self.dgtmenu.tc_blitz_map:
            logging.debug('Map-Fen: Time control blitz')
            self.dgtmenu.set_time_mode(TimeMode.BLITZ)
            self.dgtmenu.set_time_blitz(list(self.dgtmenu.tc_blitz_map.keys()).index(fen))
            text = self.dgttranslate.text('M10_tc_blitz', self.dgtmenu.tc_blitz_list[self.dgtmenu.get_time_blitz()])
            text.wait = self._exit_menu()
            timectrl = self.dgtmenu.tc_blitz_map[fen]  # type: TimeControl
            Observable.fire(Event.SET_TIME_CONTROL(tc_init=timectrl.get_parameters(), time_text=text, show_ok=False))
        elif fen in self.dgtmenu.tc_fisch_map:
            logging.debug('Map-Fen: Time control fischer')
            self.dgtmenu.set_time_mode(TimeMode.FISCHER)
            self.dgtmenu.set_time_fisch(list(self.dgtmenu.tc_fisch_map.keys()).index(fen))
            text = self.dgttranslate.text('M10_tc_fisch', self.dgtmenu.tc_fisch_list[self.dgtmenu.get_time_fisch()])
            text.wait = self._exit_menu()
            timectrl = self.dgtmenu.tc_fisch_map[fen]  # type: TimeControl
            Observable.fire(Event.SET_TIME_CONTROL(tc_init=timectrl.get_parameters(), time_text=text, show_ok=False))
        elif fen in shutdown_map:
            logging.debug('Map-Fen: shutdown')
            self._power_off()
        elif fen in reboot_map:
            logging.debug('Map-Fen: reboot')
            self._reboot()
        elif self.drawresign_fen in drawresign_map:
            if not self._inside_menu():
                logging.debug('Map-Fen: drawresign')
                Observable.fire(Event.DRAWRESIGN(result=drawresign_map[self.drawresign_fen]))
        else:
            bit_board = chess.Board(fen + ' w - - 0 1')
            pos960 = bit_board.chess960_pos(ignore_castling=True)
            if pos960 is not None:
                if pos960 == 518 or self.dgtmenu.get_engine_has_960():
                    logging.debug('Map-Fen: New game')
                    Observable.fire(Event.NEW_GAME(pos960=pos960))
                else:
                    # self._reset_moves_and_score()
                    DispatchDgt.fire(self.dgttranslate.text('Y00_error960'))
            else:
                Observable.fire(Event.FEN(fen=fen))

    def _process_engine_ready(self, message):
        for index in range(0, len(self.dgtmenu.installed_engines)):
            if self.dgtmenu.installed_engines[index]['file'] == message.eng['file']:
                self.dgtmenu.set_engine_index(index)
        self.dgtmenu.set_engine_has_960(message.has_960)
        if not self.dgtmenu.get_confirm() or not message.show_ok:
            DispatchDgt.fire(message.eng_text)
        self.dgtmenu.set_engine_restart(False)

    def _process_engine_startup(self, message):
        self.dgtmenu.installed_engines = get_installed_engines(message.shell, message.file)
        for index in range(0, len(self.dgtmenu.installed_engines)):
            eng = self.dgtmenu.installed_engines[index]
            if eng['file'] == message.file:
                self.dgtmenu.set_engine_index(index)
                self.dgtmenu.set_engine_has_960(message.has_960)
                self.dgtmenu.set_engine_level(message.level_index)

    def _process_start_new_game(self, message):
        if self.leds_are_on:
            DispatchDgt.fire(Dgt.LIGHT_CLEAR())
            self.leds_are_on = False
        self._reset_moves_and_score()
        self.engine_finished = False
        self.time_control.reset()
        if message.newgame:
            pos960 = message.game.chess960_pos()
            game_text = 'C10_newgame' if pos960 is None or pos960 == 518 else 'C10_ucigame'
            DispatchDgt.fire(self.dgttranslate.text(game_text, str(pos960)))
        if self.dgtmenu.get_mode() in (Mode.NORMAL, Mode.OBSERVE, Mode.REMOTE):
            time_left, time_right = self.time_control.current_clock_time(flip_board=self.dgtmenu.get_flip_board())
            DispatchDgt.fire(Dgt.CLOCK_START(time_left=time_left, time_right=time_right, side=ClockSide.NONE,
                                             wait=True, devs={'ser', 'i2c', 'web'}))

    def _process_computer_move_done(self):
        if self.leds_are_on:
            DispatchDgt.fire(Dgt.LIGHT_CLEAR())
            self.leds_are_on = False
        self.last_move = self.play_move
        self.last_fen = self.play_fen
        self.last_turn = self.play_turn
        self.play_move = chess.Move.null()
        self.play_fen = None
        self.play_turn = None
        self.engine_finished = False
        if not self.dgtmenu.get_confirm():
            DispatchDgt.fire(self.dgttranslate.text('K05_okpico'))
        if self.time_control.mode == TimeMode.FIXED:  # go back to a stoped time display
            DispatchDgt.fire(Dgt.DISPLAY_TIME(force=True, wait=True, devs={'ser', 'i2c', 'web'}))

    def _process_computer_move(self, message):
        if self.leds_are_on:  # can happen in case of a book move
            logging.warning('REV2 lights still on')
            DispatchDgt.fire(Dgt.LIGHT_CLEAR())
        move = message.move
        ponder = message.ponder
        # fen = message.fen
        # turn = message.turn
        self.engine_finished = True
        self.play_move = move
        self.play_fen = message.game.fen()
        self.play_turn = message.game.turn
        if ponder:
            game_copy = message.game.copy()
            game_copy.push(move)
            self.hint_move = ponder
            self.hint_fen = game_copy.fen()
            self.hint_turn = game_copy.turn
        else:
            self.hint_move = chess.Move.null()
            self.hint_fen = None
            self.hint_turn = None
        # Display the move
        side = self._get_clock_side(message.game.turn)
        disp = Dgt.DISPLAY_MOVE(move=move, fen=message.game.fen(), side=side, wait=message.wait, maxtime=0,
                                beep=self.dgttranslate.bl(BeepLevel.CONFIG), devs={'ser', 'i2c', 'web'})
        DispatchDgt.fire(disp)
        DispatchDgt.fire(Dgt.LIGHT_SQUARES(uci_move=move.uci()))
        self.leds_are_on = True

    def _process_user_move_done(self, message):
        if self.leds_are_on:  # can happen in case of a sliding move
            logging.warning('REV2 lights still on')
            DispatchDgt.fire(Dgt.LIGHT_CLEAR())
            self.leds_are_on = False
        self.last_move = message.move
        self.last_fen = message.fen
        self.last_turn = message.turn
        self.engine_finished = False
        if not self.dgtmenu.get_confirm():
            DispatchDgt.fire(self.dgttranslate.text('K05_okuser'))

    def _process_review_move_done(self, message):
        if self.leds_are_on:  # can happen in case of a sliding move
            logging.warning('REV2 lights still on')
            DispatchDgt.fire(Dgt.LIGHT_CLEAR())
            self.leds_are_on = False
        self.last_move = message.move
        self.last_fen = message.fen
        self.last_turn = message.turn
        if not self.dgtmenu.get_confirm():
            DispatchDgt.fire(self.dgttranslate.text('K05_okmove'))

    def _process_time_control(self, message):
        if not self.dgtmenu.get_confirm() or not message.show_ok:
            DispatchDgt.fire(message.time_text)
        timectrl = self.time_control = TimeControl(**message.tc_init)
        time_left, time_right = timectrl.current_clock_time(flip_board=self.dgtmenu.get_flip_board())
        DispatchDgt.fire(Dgt.CLOCK_START(time_left=time_left, time_right=time_right, side=ClockSide.NONE, wait=True,
                                         devs={'ser', 'i2c', 'web'}))

    def _process_new_score(self, message):
        if message.mate is None:
            score = int(message.score)
            if message.turn == chess.BLACK:
                score *= -1
            text = self.dgttranslate.text('N10_score', score)
        else:
            text = self.dgttranslate.text('N10_mate', str(message.mate))
        self.score = text
        if message.mode == Mode.KIBITZ and not self._inside_menu():
            DispatchDgt.fire(self._combine_depth_and_score())

    def _process_new_pv(self, message):
        self.hint_move = message.pv[0]
        self.hint_fen = message.game.fen()
        self.hint_turn = message.game.turn
        if message.mode == Mode.ANALYSIS and not self._inside_menu():
            side = self._get_clock_side(self.hint_turn)
            disp = Dgt.DISPLAY_MOVE(move=self.hint_move, fen=self.hint_fen, side=side, wait=True, maxtime=0,
                                    beep=self.dgttranslate.bl(BeepLevel.NO), devs={'ser', 'i2c', 'web'})
            DispatchDgt.fire(disp)

    def _process_startup_info(self, message):
        self.dgtmenu.set_mode(message.info['interaction_mode'])
        self.dgtmenu.set_book(message.info['book_index'])
        self.dgtmenu.all_books = message.info['books']
        timectrl = self.time_control = message.info['time_control']
        self.dgtmenu.set_time_mode(timectrl.mode)
        # try to find the index from the given time_control (timectrl)
        # if user gave a non-existent timectrl value stay at standard
        index = 0
        if timectrl.mode == TimeMode.FIXED:
            for val in self.dgtmenu.tc_fixed_map.values():
                if val == timectrl:
                    self.dgtmenu.set_time_fixed(index)
                    break
                index += 1
        elif timectrl.mode == TimeMode.BLITZ:
            for val in self.dgtmenu.tc_blitz_map.values():
                if val == timectrl:
                    self.dgtmenu.set_time_blitz(index)
                    break
                index += 1
        elif timectrl.mode == TimeMode.FISCHER:
            for val in self.dgtmenu.tc_fisch_map.values():
                if val == timectrl:
                    self.dgtmenu.set_time_fisch(index)
                    break
                index += 1

    def _process_clock_start(self, message):
        timectrl = self.time_control = TimeControl(**message.tc_init)
        if timectrl.mode == TimeMode.FIXED:
            time_left = time_right = timectrl.seconds_per_move
        else:
            time_left, time_right = timectrl.current_clock_time(flip_board=self.dgtmenu.get_flip_board())
            if time_left < 0:
                time_left = 0
            if time_right < 0:
                time_right = 0
        side = self._get_clock_side(message.turn)
        DispatchDgt.fire(Dgt.CLOCK_START(time_left=time_left, time_right=time_right, side=side, wait=False,
                                         devs=message.devs))

    def _process_dgt_serial_nr(self):
        # logging.debug('Serial number {}'.format(message.number))  # actually used for watchdog (once a second)
        if self.dgtmenu.get_mode() == Mode.PONDER and not self._inside_menu():
            if self.show_move_or_value >= self.dgtmenu.get_ponderinterval():
                if self.hint_move:
                    side = self._get_clock_side(self.hint_turn)
                    text = Dgt.DISPLAY_MOVE(move=self.hint_move, fen=self.hint_fen, side=side, wait=True, maxtime=1,
                                            beep=self.dgttranslate.bl(BeepLevel.NO), devs={'ser', 'i2c', 'web'})
                else:
                    text = self.dgttranslate.text('N10_nomove')
            else:
                text = self._combine_depth_and_score()
            text.wait = True
            DispatchDgt.fire(text)
            self.show_move_or_value = (self.show_move_or_value + 1) % (self.dgtmenu.get_ponderinterval() * 2)

    def _drawresign(self):
        _, _, _, rnk_5, rnk_4, _, _, _ = self.dgtmenu.get_dgt_fen().split('/')
        return '8/8/8/' + rnk_5 + '/' + rnk_4 + '/8/8/8'

    def _exit_display(self):
        if self.play_move and self.dgtmenu.get_mode() in (Mode.NORMAL, Mode.REMOTE):
            side = self._get_clock_side(self.play_turn)
            text = Dgt.DISPLAY_MOVE(move=self.play_move, fen=self.play_fen, side=side, wait=True, maxtime=1,
                                    beep=self.dgttranslate.bl(BeepLevel.BUTTON), devs={'ser', 'i2c', 'web'})
        else:
            text = None
            if self._inside_menu():
                text = self.dgtmenu.get_current_text()
            if text:
                text.wait = True  # in case of "bad pos" message send before
            else:
                text = Dgt.DISPLAY_TIME(force=True, wait=True, devs={'ser', 'i2c', 'web'})
        DispatchDgt.fire(text)

    def _process_message(self, message):
        for case in switch(message):
            if case(MessageApi.ENGINE_READY):
                self._process_engine_ready(message)
                break
            if case(MessageApi.ENGINE_STARTUP):
                self._process_engine_startup(message)
                break
            if case(MessageApi.ENGINE_FAIL):
                DispatchDgt.fire(self.dgttranslate.text('Y00_erroreng'))
                break
            if case(MessageApi.COMPUTER_MOVE):
                self._process_computer_move(message)
                break
            if case(MessageApi.START_NEW_GAME):
                self._process_start_new_game(message)
                break
            if case(MessageApi.COMPUTER_MOVE_DONE):
                self._process_computer_move_done()
                break
            if case(MessageApi.USER_MOVE_DONE):
                self._process_user_move_done(message)
                break
            if case(MessageApi.REVIEW_MOVE_DONE):
                self._process_review_move_done(message)
                break
            if case(MessageApi.ALTERNATIVE_MOVE):
                if self.leds_are_on:
                    DispatchDgt.fire(Dgt.LIGHT_CLEAR())
                    self.leds_are_on = False
                DispatchDgt.fire(self.dgttranslate.text('B05_altmove'))
                break
            if case(MessageApi.LEVEL):
                if not self.dgtmenu.get_engine_restart():
                    DispatchDgt.fire(message.level_text)
                break
            if case(MessageApi.TIME_CONTROL):
                self._process_time_control(message)
                break
            if case(MessageApi.OPENING_BOOK):
                if not self.dgtmenu.get_confirm() or not message.show_ok:
                    DispatchDgt.fire(message.book_text)
                break
            if case(MessageApi.TAKE_BACK):
                if self.leds_are_on:
                    DispatchDgt.fire(Dgt.LIGHT_CLEAR())
                    self.leds_are_on = False
                self._reset_moves_and_score()
                self.engine_finished = False
                DispatchDgt.fire(self.dgttranslate.text('C10_takeback'))
                break
            if case(MessageApi.GAME_ENDS):
                if not self.dgtmenu.get_engine_restart():  # filter out the shutdown/reboot process
                    text = self.dgttranslate.text(message.result.value)
                    text.beep = self.dgttranslate.bl(BeepLevel.CONFIG)
                    text.maxtime = 0.5
                    DispatchDgt.fire(text)
                break
            if case(MessageApi.INTERACTION_MODE):
                # self.dgtmenu.set_mode(message.mode)
                self.engine_finished = False
                if not self.dgtmenu.get_confirm() or not message.show_ok:
                    DispatchDgt.fire(message.mode_text)
                break
            if case(MessageApi.PLAY_MODE):
                DispatchDgt.fire(message.play_mode_text)
                break
            if case(MessageApi.NEW_SCORE):
                self._process_new_score(message)
                break
            if case(MessageApi.BOOK_MOVE):
                self.score = self.dgttranslate.text('N10_score', None)
                DispatchDgt.fire(self.dgttranslate.text('N10_bookmove'))
                break
            if case(MessageApi.NEW_PV):
                self._process_new_pv(message)
                break
            if case(MessageApi.NEW_DEPTH):
                self.depth = message.depth
                break
            if case(MessageApi.IP_INFO):
                self.dgtmenu.int_ip = message.info['int_ip']
                self.dgtmenu.ext_ip = message.info['ext_ip']
                break
            if case(MessageApi.STARTUP_INFO):
                self._process_startup_info(message)
                break
            if case(MessageApi.SEARCH_STARTED):
                logging.debug('Search started')
                break
            if case(MessageApi.SEARCH_STOPPED):
                logging.debug('Search stopped')
                break
            if case(MessageApi.CLOCK_START):
                self._process_clock_start(message)
                break
            if case(MessageApi.CLOCK_STOP):
                DispatchDgt.fire(Dgt.CLOCK_STOP(devs=message.devs))
                break
            if case(MessageApi.DGT_BUTTON):
                self._process_button(message)
                break
            if case(MessageApi.DGT_FEN):
                self._process_fen(message.fen, message.raw)
                break
            if case(MessageApi.DGT_CLOCK_VERSION):
                if message.dev == 'ser':  # send the "board connected message" to serial clock
                    DispatchDgt.fire(message.text)
                time_left, time_right = self.time_control.current_clock_time(flip_board=self.dgtmenu.get_flip_board())
                DispatchDgt.fire(Dgt.CLOCK_START(time_left=time_left, time_right=time_right, side=ClockSide.NONE,
                                                 wait=True, devs={message.dev}))
                DispatchDgt.fire(Dgt.CLOCK_VERSION(main=message.main, sub=message.sub, dev=message.dev))
                break
            if case(MessageApi.DGT_CLOCK_TIME):
                DispatchDgt.fire(Dgt.CLOCK_TIME(time_left=message.time_left, time_right=message.time_right,
                                                dev=message.dev))
                break
            if case(MessageApi.DGT_SERIAL_NR):
                self._process_dgt_serial_nr()
                break
            if case(MessageApi.DGT_JACK_CONNECTED_ERROR):  # this will only work in case of 2 clocks connected!
                DispatchDgt.fire(self.dgttranslate.text('Y00_errorjack'))
                break
            if case(MessageApi.DGT_EBOARD_VERSION):
                DispatchDgt.fire(message.text)
                DispatchDgt.fire(Dgt.DISPLAY_TIME(force=True, wait=True, devs={'i2c'}))
                break
            if case(MessageApi.DGT_NO_EBOARD_ERROR):
                DispatchDgt.fire(message.text)
                break
            if case(MessageApi.DGT_NO_CLOCK_ERROR):
                break
            if case(MessageApi.SWITCH_SIDES):
                self.engine_finished = False
                self.hint_move = chess.Move.null()
                self.hint_fen = None
                self.hint_turn = None
                logging.debug('user ignored move {}'.format(message.move))
                break
            if case(MessageApi.EXIT_MENU):
                self._exit_display()
                break
            if case(MessageApi.WRONG_FEN):
                DispatchDgt.fire(self.dgttranslate.text('C10_setpieces'))
                break
            if case():  # Default
                # print(message)
                pass

    def run(self):
        """called from threading.Thread by its start() function."""
        logging.info('msg_queue ready')
        while True:
            # Check if we have something to display
            try:
                message = self.msg_queue.get()
                if repr(message) != MessageApi.DGT_SERIAL_NR:
                    logging.debug("received message from msg_queue: %s", message)
                self._process_message(message)
            except queue.Empty:
                pass
