from io import StringIO

from .context import GCodeCommand, GCodeBlock


def test_gcode_command_compress():
    command = GCodeCommand(code='G28', parameters=['X0', 'Y0'])
    compressed = command.compress()
    assert compressed == 'G28 X0 Y0'

def test_gcode_command_write_with_comments():
    command = GCodeCommand(code='G1', parameters=['X10', 'Y20'])
    command.comment = ['Move to position']
    fp = StringIO()
    command.write(fp)
    expected_output = 'G1 X10 Y20                                                ; Move to position\n'
    assert fp.getvalue() == expected_output

def test_gcode_command_write_without_comments():
    command = GCodeCommand(code='G28', parameters=[])
    fp = StringIO()
    command.write(fp)
    expected_output = 'G28\n'
    assert fp.getvalue() == expected_output

def test_gcode_block_write():
    block = GCodeBlock()
    block.comment = ['This is a block of GCode commands']
    command1 = GCodeCommand(code='G28', parameters=[])
    command2 = GCodeCommand(code='G1', parameters=['X10', 'Y20'])
    block.code = [command1, command2]
    fp = StringIO()
    block.write(fp)
    expected_output = '; This is a block of GCode commands\nG28\nG1 X10 Y20\n\n'
    assert fp.getvalue() == expected_output

def test_gcode_block_parse():
    lines = [
        '; This is a block of GCode commands',
        'G28',
        'G1 X10 Y20 ; Move to position',
        '',
    ]
    block = GCodeBlock().parse(lines)
    assert block.comment == ['This is a block of GCode commands']
    assert len(block.code) == 2
    assert block.code[0].code == 'G28'
    assert block.code[0].parameters == ['']
    assert block.code[0].comment == []
    assert block.code[1].code == 'G1'
    assert block.code[1].parameters == ['X10 Y20']
    assert block.code[1].comment == ['Move to position']


def test_gcode_command_compress_empty():
    """Test compress with empty code and params returns empty string."""
    command = GCodeCommand(code='', parameters=[])
    assert command.compress() == ''


def test_gcode_command_write_empty():
    """Test write with empty code and params writes empty line."""
    command = GCodeCommand(code='', parameters=[])
    fp = StringIO()
    command.write(fp)
    assert fp.getvalue() == '\n'


def test_gcode_command_write_multiple_comments():
    """Test write with multiple comments."""
    command = GCodeCommand(code='G1', parameters=['X10'], comment=['first', 'second'])
    fp = StringIO()
    command.write(fp)
    output = fp.getvalue()
    assert 'first' in output
    assert 'second' in output
    # first comment inline, second on separate ; line
    lines = output.splitlines()
    assert len(lines) == 2
    assert lines[1].strip().startswith(';')


def test_gcode_command_write_comment_only():
    """Test write with comment but no code."""
    command = GCodeCommand(code='', parameters=[], comment=['just a comment'])
    fp = StringIO()
    command.write(fp)
    assert fp.getvalue() == ';just a comment\n'


def test_gcode_block_parse_indented_comment():
    """Test parse appends indented comments to previous command."""
    lines = [
        'G28',
        '    ; indented continuation',
    ]
    block = GCodeBlock().parse(lines)
    assert len(block.code) == 1
    assert block.code[0].code == 'G28'
    assert 'indented continuation' in block.code[0].comment


def test_gcode_block_parse_disabled_gcode():
    """Test parse handles disabled gcode lines (;G28, ;M104)."""
    lines = [
        ';G28 X Y',
    ]
    block = GCodeBlock().parse(lines)
    assert len(block.code) == 1
    assert block.code[0].code == ''


def test_gcode_block_parse_indented_comment_no_previous_command():
    """Test parse handles indented comment with no previous command."""
    lines = [
        '    ; orphan indented comment',
    ]
    block = GCodeBlock().parse(lines)
    # Should not crash, comment goes to block comment or current_comment
    assert len(block.code) == 0