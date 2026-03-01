"""GCode parsing and writing utilities."""
import re
from typing import List, TextIO

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import attr

# Column at which inline comments are aligned
COMMENT_ALIGNMENT_COLUMN = 60

# Indentation threshold for continuation comments
CONTINUATION_INDENT_THRESHOLD = 4


@attr.s
class GCodeCommand():
    """A single GCode command."""

    code = attr.ib(type=str)
    comment = attr.ib(factory=list)
    parameters = attr.ib(factory=list)

    def compress(self) -> str:
        """Compress the GCodeCommand into a single line without comments."""
        gcode_line = '{0} {1}'.format(
            self.code,
            ' '.join(self.parameters),
        )
        if gcode_line == ' ':
            gcode_line = ''

        return gcode_line

    def write(self, fp: TextIO) -> None:
        """Write the GCodeCommand to a file-like object."""
        gcode_line = '{0} {1}'.format(
            self.code,
            ' '.join(self.parameters),
        )
        if gcode_line == ' ':
            gcode_line = ''

        if len(self.comment) > 1:
            fp.write(gcode_line.rstrip())
            if len(self.code) > 0:
                fp.write(' ; '.rjust(max(1, COMMENT_ALIGNMENT_COLUMN - len(gcode_line))))
            fp.write(self.comment[0])
            fp.write('\n')
            for c in self.comment[1:]:
                fp.write('; '.rjust(COMMENT_ALIGNMENT_COLUMN))
                fp.write(c)
                fp.write('\n')
        elif len(self.comment) == 1:
            fp.write(gcode_line.rstrip())
            if len(self.code) > 0:
                fp.write(' ; '.rjust(max(1, COMMENT_ALIGNMENT_COLUMN - len(gcode_line))))
            else:
                fp.write(';')
            fp.write(self.comment[0])
            fp.write('\n')
        else:
            fp.write(gcode_line.rstrip())
            fp.write('\n')


@attr.s
class GCodeBlock():
    """A block of GCode commands."""

    comment = attr.ib(type=List[str], factory=list)
    code = attr.ib(type=List[GCodeCommand], factory=list)

    def write(self, fp: TextIO) -> None:
        """Write the GCodeBlock to a file-like object."""
        if len(self.comment) > 0:
            fp.write('; ')
            fp.write('\n; '.join(self.comment))
            fp.write('\n')

        for code in self.code:
            code.write(fp)

        fp.write('\n')

    def parse(self, lines: List) -> Self:
        """Parse a list of lines into a GCodeBlock."""
        current_comment = []

        gcode_regex = re.compile(
            r"([a-zA-Z0-9_\.\(\)\'\"\=\! \[\]\|\/\-\+\<\>\&,\{\}\*\^\:]*)"
            r";?(.*)\n?",
        )

        for line in lines:
            gcode_match = gcode_regex.match(line.strip())
            line_gcode = gcode_match.group(1).strip()
            line_comment = gcode_match.group(2)

            if line_comment != '':
                if line_gcode == '' and (len(line) - len(line.lstrip())) >= CONTINUATION_INDENT_THRESHOLD:
                    try:
                        self.code[-1].comment.append(line_comment.strip())
                    except IndexError:
                        current_comment.append(line_comment.strip())
                else:
                    current_comment.append(line_comment.strip())

            if line_gcode != '':
                gcode = line_gcode.split(' ')
                self.code.append(
                    GCodeCommand(
                        code=gcode[0],
                        comment=current_comment,
                        parameters=[' '.join(gcode[1:])],
                    ),
                )
                current_comment = []
            elif len(line_comment) > 0 and line_comment[0] in ['G', 'M', 'T']:
                # this is a hidden/disabled gcode line and not a comment
                self.code.append(
                    GCodeCommand(
                        code='',
                        comment=current_comment,
                        parameters='',
                    ),
                )
                current_comment = []
            if line_gcode == '' and len(self.code) == 0:
                # comment only block
                self.comment.extend(current_comment)
                current_comment = []
        return self
