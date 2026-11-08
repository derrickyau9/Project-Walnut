"""Small offline full-Pinyin decoder backed by a redistributable Rime dictionary.

Only Python's standard library is required. This is a deterministic phrase IME,
not a neural or cloud predictor. The dictionary is opened read-only; typed text
and passwords are never written to disk.
"""
from dataclasses import dataclass
from functools import lru_cache
from math import log
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class Candidate:
    text: str
    consumed: int
    spelling: str


class PinyinEngine:
    PAGE_SIZE = 9
    MAX_INPUT = 64

    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).with_name('ime_data') / 'pinyin_simp.sqlite3')
        self.connection = None
        self.error = ''
        self.entry_count = 0
        try:
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
            self.connection.execute('PRAGMA query_only=ON')
            self.entry_count = int(self.connection.execute(
                "SELECT value FROM metadata WHERE name='entry_count'").fetchone()[0])
        except (OSError, sqlite3.Error, TypeError) as error:
            self.error = 'Pinyin dictionary unavailable: ' + str(error)
            if self.connection is not None:
                self.connection.close()
            self.connection = None

    @property
    def available(self):
        return self.connection is not None

    @staticmethod
    def normalize(text):
        return re.sub('[^a-z]', '', text.lower().replace('u:', 'v').replace('ü', 'v'))

    @staticmethod
    def _positions(raw):
        """Map joined spelling positions back to the raw apostrophe spelling."""
        positions = [0]
        boundaries = set()
        for index, character in enumerate(raw):
            if character == "'":
                boundaries.add(len(positions) - 1)
            else:
                positions.append(index + 1)
        # Consume a separator together with the preceding syllable.
        for index in range(1, len(positions)):
            while positions[index] < len(raw) and raw[positions[index]] == "'":
                positions[index] += 1
        return positions, boundaries

    @lru_cache(maxsize=4096)
    def _exact(self, key):
        if not self.available:
            return ()
        return tuple(self.connection.execute(
            'SELECT text,spelling,weight FROM entries WHERE key=? '
            'ORDER BY weight DESC,text LIMIT 256', (key,)))

    @staticmethod
    def _respects_boundaries(spelling, start, end, boundaries):
        syllable_ends = set()
        offset = start
        for syllable in spelling.split():
            offset += len(syllable)
            syllable_ends.add(offset)
        return all(point in syllable_ends for point in boundaries if start < point < end)

    def _sentence(self, key, boundaries):
        # Dynamic programming over known words. A word penalty favours useful
        # phrases over a string of independently popular homophone characters.
        best = {len(key): (0.0, '', '')}
        for start in range(len(key) - 1, -1, -1):
            choices = []
            for end in range(start + 1, min(len(key), start + 32) + 1):
                if end not in best:
                    continue
                rows = self._exact(key[start:end])
                row = next((row for row in rows if self._respects_boundaries(
                    row[1], start, end, boundaries)), None)
                if row is None:
                    continue
                tail = best[end]
                choices.append((log(row[2] + 1) - 17.5 + tail[0],
                                row[0] + tail[1], row[1] + (' ' + tail[2] if tail[2] else '')))
            if choices:
                best[start] = max(choices, key=lambda choice: choice[0])
        return best.get(0)

    @lru_cache(maxsize=128)
    def lookup(self, raw):
        raw = raw.lower().replace('ü', 'v').replace('u:', 'v')
        if not self.available or not re.fullmatch("[a-z']{1,64}", raw):
            return ()
        key = self.normalize(raw)
        if not key:
            return ()
        positions, boundaries = self._positions(raw)
        candidates = []
        seen = set()

        def add(text, consumed, spelling):
            marker = (text, consumed)
            if marker not in seen:
                candidates.append(Candidate(text, consumed, spelling))
                seen.add(marker)

        # Full dictionary words rank first; then a decoded multi-word sentence.
        for text, spelling, _ in self._exact(key):
            if self._respects_boundaries(spelling, 0, len(key), boundaries):
                add(text, len(raw), spelling)
        sentence = self._sentence(key, boundaries)
        if sentence:
            add(sentence[1], len(raw), sentence[2])

        # Incomplete final syllables can still offer whole-word completions.
        # Prefix range uses the index, rather than scanning the whole dictionary.
        if len(key) >= 2 and len(candidates) < 90:
            rows = self.connection.execute(
                'SELECT text,spelling,weight FROM entries WHERE key>=? AND key<? '
                'ORDER BY weight DESC LIMIT 90', (key, key + '{'))
            for text, spelling, _ in rows:
                if self._respects_boundaries(spelling, 0, len(key), boundaries):
                    add(text, len(raw), spelling)
        # Also allow selecting the first word, leaving the remaining spelling
        # composed. This is essential when the sentence guess is incorrect.
        for length in range(len(key) - 1, 0, -1):
            for text, spelling, _ in self._exact(key[:length])[:18]:
                if self._respects_boundaries(spelling, 0, length, boundaries):
                    add(text, positions[length], spelling)
        return tuple(candidates[:90])
