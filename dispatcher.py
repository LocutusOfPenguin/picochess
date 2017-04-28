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

from utilities import DisplayDgt, DispatchDgt, dispatch_queue
import logging
import queue
from dgt.api import Dgt, DgtApi
from threading import Timer, Thread, Lock


class Dispatcher(DispatchDgt, Thread):

    """a dispatcher taking the dispatch_queue and fill dgt_queue with the commands in time."""

    def __init__(self):
        super(Dispatcher, self).__init__()

        self.maxtimer = None
        self.maxtimer_running = False
        self.time_factor = 1  # This is for testing the duration - remove it lateron!
        self.tasks = []  # delayed task array

        self.display_hash = None  # Hash value of clock's display
        self.process_lock = Lock()

    def _stopped_maxtimer(self):
        self.maxtimer_running = False
        # if self.clock_running:
        #     logging.debug('showing the running clock again')
        #     DisplayDgt.show(Dgt.DISPLAY_TIME(force=False, wait=True, devs={'ser', 'i2c', 'web'}))
        # else:
        #     logging.debug('clock not running - ignored maxtime')

        # @todo we try it without this test from above - since dispatcher doesnt know if clock is running anyway
        DisplayDgt.show(Dgt.DISPLAY_TIME(force=False, wait=True, devs={'ser', 'i2c', 'web'}))
        if self.tasks:
            logging.debug('processing delayed tasks: {}'.format(self.tasks))
        while self.tasks:
            message = self.tasks.pop(0)
            with self.process_lock:
                self._process_message(message)
            if self.maxtimer_running:  # run over the task list until a maxtime command was processed
                break

    def _process_message(self, message):
        do_handle = True
        if repr(message) in (DgtApi.CLOCK_START, DgtApi.CLOCK_STOP, DgtApi.CLOCK_TIME):
            self.display_hash = None  # Cant know the clock display if command changing the running status
        else:
            if repr(message) in (DgtApi.DISPLAY_MOVE, DgtApi.DISPLAY_TEXT):
                if self.display_hash == hash(message) and not message.beep:
                    do_handle = False
                else:
                    self.display_hash = hash(message)

        if do_handle:
            logging.debug("handle DgtApi: {} at {}".format(message, self.__class__.__name__))
            if hasattr(message, 'maxtime') and message.maxtime > 0:
                self.maxtimer = Timer(message.maxtime * self.time_factor, self._stopped_maxtimer)
                self.maxtimer.start()
                logging.debug('showing {} for {} secs'.format(message, message.maxtime * self.time_factor))
                self.maxtimer_running = True
            DisplayDgt.show(message)
        else:
            logging.debug("ignore DgtApi: {} at {}".format(message, self.__class__.__name__))

    def run(self):
        """called from threading.Thread by its start() function."""
        logging.info('dispatch_queue ready')
        while True:
            # Check if we have something to display
            try:
                message = dispatch_queue.get()
                logging.debug("received command from dispatch_queue: %s", message)

                if self.maxtimer_running:
                    if hasattr(message, 'wait'):
                        if message.wait:
                            self.tasks.append(message)
                            logging.debug('tasks delayed: {}'.format(self.tasks))
                            continue
                        else:
                            logging.debug('ignore former maxtime')
                            self.maxtimer.cancel()
                            self.maxtimer.join()
                            self.maxtimer_running = False
                            if self.tasks:
                                logging.debug('delete following tasks: {}'.format(self.tasks))
                                self.tasks = []
                    else:
                        logging.debug('command doesnt change the clock display => no need to interrupt max timer')
                else:
                    logging.debug('max timer not running => process command')

                with self.process_lock:
                    self._process_message(message)
            except queue.Empty:
                pass
