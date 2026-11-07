"""Self-contained fatal-aware launcher template; never changes modeled time."""
import hashlib
import os
from pathlib import Path
import struct
import sys

LAUNCHER_POLICY = 'fatal-ELF-exec-v2'

FATAL_SYMBOLS = {'z_fatal_error', 'z_riscv_fatal_error', 'k_sys_fatal_error_handler',
                 'arch_system_halt', '__assert_func', '__stack_chk_fail', 'abort'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def fatal_addresses(path):
    data = Path(path).read_bytes()
    def unpack(fmt, offset):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data): raise ValueError('Truncated ELF structure')
        return struct.unpack_from(fmt, data, offset)
    header = unpack('<16sHHIQQQIHHHHHH', 0)
    if header[0][:7] != b'\x7fELF\x02\x01\x01' or header[2] != 243:
        raise ValueError('RV64 little-endian ELF required')
    if header[1] != 2 or header[3] != 1 or header[8] != 64:
        raise ValueError('Current-version static executable ELF required')
    if header[9] != 56 or not header[10] or not header[5]:
        raise ValueError('ELF executable load segments required')
    segments = [unpack('<IIQQQQQQ', header[5] + i * header[9]) for i in range(header[10])]
    executable = [row for row in segments if row[0] == 1 and row[1] & 1]
    for row in segments:
        if row[0] == 1 and (row[5] > row[6] or row[2] > len(data) or row[5] > len(data) - row[2]):
            raise ValueError('Invalid ELF load segment bounds')
    if not executable or any(p[0] == 3 for p in segments): raise ValueError('Static ELF executable load segment required')
    if header[11] != 64 or not header[12] or not header[6]:
        raise ValueError('ELF section table required')
    sections = [unpack('<IIQQQQIIQQ', header[6] + i * header[11]) for i in range(header[12])]
    if not any(row[1] == 2 for row in sections):
        raise ValueError('Unstripped symbol table required for automatic fatal-handler resolution')
    def section(row):
        offset, size = row[4], row[5]
        if offset > len(data) or size > len(data) - offset: raise ValueError('Truncated ELF section')
        return data[offset:offset + size]
    result = {}
    for row in sections:
        if row[1] not in (2, 11): continue
        if (row[9] != 24 or row[5] % 24 or row[6] >= len(sections)
                or sections[row[6]][1] != 3):
            raise ValueError('Invalid ELF symbol table')
        table, strings = section(row), section(sections[row[6]])
        for offset in range(0, len(table), 24):
            name, info, other, index, value, size = struct.unpack_from('<IBBHQQ', table, offset)
            if name >= len(strings): raise ValueError('Invalid ELF symbol name')
            end = strings.find(b'\0', name)
            if end < 0: raise ValueError('Unterminated ELF symbol name')
            text = strings[name:end].decode('utf-8', errors='strict')
            if text not in FATAL_SYMBOLS or index == 0: continue
            if (info & 15) not in (0, 2) or (info >> 4) not in (0, 1, 2):
                raise ValueError('Fatal symbol must name a defined code function')
            if index >= 0xff00 or index >= len(sections):
                raise ValueError('Fatal symbol has reserved/out-of-range section index')
            code = sections[index]
            if (code[1] != 1 or code[2] & 6 != 6 or not value or value & 1
                    or not code[3] <= value < code[3] + code[5]
                    or size > code[3] + code[5] - value
                    or not any(p[3] <= value < p[3] + p[5] and size <= p[3]+p[5]-value
                        and code[4]+value-code[3] == p[2]+value-p[3] for p in executable)):
                raise ValueError('Fatal symbol lies outside allocated executable ELF bytes')
            if text in result and result[text] != value:
                raise ValueError('Ambiguous duplicate fatal symbol: ' + text)
            result[text] = value
    return result


def launch(native, native_sha256, python, python_sha256, arguments):
    try:
        native, python = Path(native), Path(python)
        if not sys.flags.isolated or not sys.flags.no_site:
            raise ValueError('Isolated Python -IS execution required')
        if Path(sys.executable).resolve() != python.resolve() or sha(python) != python_sha256:
            raise ValueError('Selected Python interpreter changed')
        if sha(native) != native_sha256: raise ValueError('Selected native binary changed')
        if not arguments: raise ValueError('Guest ELF argument required')
        elf = Path(arguments[-1]).resolve()
        if not elf.is_file(): raise ValueError('Last argument must be the guest ELF')
        before = sha(elf); configured = set(fatal_addresses(elf).values())
        raw = os.environ.get('IRIS_FATAL_PCS', '')
        if raw:
            for token in raw.split(','):
                value = int(token, 0)
                if not 0 <= value < 1 << 64: raise ValueError('Invalid explicit fatal PC')
                configured.add(value)
        environment = dict(os.environ)
        if configured: environment['IRIS_FATAL_PCS'] = ','.join(hex(v) for v in sorted(configured))
        if sha(elf) != before or sha(native) != native_sha256: raise ValueError('Input changed before native exec')
        os.execve(str(native), [str(native), *arguments], environment)
    except (OSError, ValueError, UnicodeError, struct.error) as error:
        print('IRIS_LAUNCH_REFUSED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)


def launcher_text(native, native_sha256, python, python_sha256):
    """Include this exact source in the wrapper; runtime needs no repo imports."""
    source = Path(__file__).read_text()
    return ('#!' + str(python) + ' -IS\n' + source +
            '\nif __name__ == "__main__":\n    launch(' + repr(str(native)) + ', ' +
            repr(native_sha256) + ', ' + repr(str(python)) + ', ' + repr(python_sha256) + ', sys.argv[1:])\n')
