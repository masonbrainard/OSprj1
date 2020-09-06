#!/usr/bin/env python3
import argparse
import pathlib
import re
import os
import string
import subprocess
import sys
import tempfile

import hypothesis.strategies as st
from hypothesis import example, given


class KeptTemporaryDirectory:
    def __init__(self, *args, **kwargs):
        self.name = tempfile.mkdtemp(*args, **kwargs)

    def __enter__(self):
        return pathlib.Path(self.name)

    def __exit__(self, *args):
        pass


class BaseFile:
    repr_attributes = ['name']
    repr_star = None
    default_permissions = 0o600

    def __init__(self, name, permissions=None):
        self.name = name
        if permissions is not None:
            self.permissions = permissions
        else:
            self.permissions = self.default_permissions

        if name is not None:
            if '/' in name:
                raise ValueError('Files may not contain / in the name')
            if name in ('.', '..'):
                raise ValueError('Names may not be "." or ".."')

    def count_expected_errors(self):
        return 0

    def __repr__(self):
        return "{}({})".format(
            self.__class__.__name__,
            ', '.join(
                [*(repr(getattr(self, attr, '???')) for attr in self.repr_attributes),
                 *(map(repr, getattr(self, self.repr_star))
                   if self.repr_star else ()),
                 *(('permissions=0o{:o}'.format(self.permissions),)
                   if self.permissions != self.default_permissions else ())]))


class File(BaseFile):
    default_permissions = 0o400

    def __init__(self, name, contents='', permissions=None):
        self.contents = contents
        super().__init__(name, permissions=permissions)

    def create(self, base_dir):
        full_path = base_dir / self.name
        with open(full_path, 'w') as f:
            f.write(self.contents)
        full_path.chmod(self.permissions)

    def __eq__(self, other):
        return isinstance(other, File) and self.name == other.name


class Dir(BaseFile):
    repr_attributes = ['name']
    repr_star = 'entries'
    default_permissions = 0o700

    def __init__(self, name, *entries, permissions=None):
        entry_names = [e.name for e in entries]
        if len(entry_names) != len(set(entry_names)):
            raise ValueError(
                "Directories cannot contain entries with non-unique names")
        self.entries = entries
        super().__init__(name, permissions=permissions)

    def create(self, base_dir):
        full_path = base_dir / self.name
        full_path.mkdir(mode=self.permissions)
        for entry in self.entries:
            entry.create(full_path)

    def count_expected_errors(self):
        if self.permissions & 0o400:
            return sum(e.count_expected_errors() for e in self.entries)
        return 1

    def max_path_length(self):
        max_len = 0
        for e in self.entries:
            sublen = len(e.name)
            if isinstance(e, Dir):
                sublen = e.max_path_length()
            if sublen > max_len:
                max_len = sublen
        self_len = 0
        if self.name is not None:
            self_len = len(self.name) + 1
        return max_len + self_len

    def as_dict(self):
        return {e.name: e for e in self.entries}

    def __eq__(self, other):
        if not isinstance(other, Dir):
            return False

        return self.name == other.name and self.as_dict() == other.as_dict()


class FileOrEmptyDir(File, Dir):
    default_permissions = 0

    def __init__(self, name):
        super().__init__(name)

    def create(self, base_dir):
        raise NotImplementedError('FileOrEmptyDir should not be created')

    def __eq__(self, other):
        if isinstance(other, Dir):
            return self.name == other.name and not other.entries
        if isinstance(other, File):
            return self.name == other.name
        return False


class RootDir(Dir):
    repr_attributes = []

    def __init__(self, *entries, permissions=None):
        super().__init__(None, *entries, permissions=permissions)

    def create(self, base_dir):
        # Directory is assumed to exist for root directory.
        base_dir.chmod(self.permissions)
        for entry in self.entries:
            entry.create(base_dir)

    def tempdir(self):
        if os.environ.get('KEEP_TEMPFILES'):
            t = KeptTemporaryDirectory()
        else:
            t = tempfile.TemporaryDirectory()

        self.create(pathlib.Path(t.name))
        return t

    def __eq__(self, other):
        if not isinstance(other, RootDir):
            return False

        return self.as_dict() == other.as_dict()



class Symlink(BaseFile):
    repr_attributes = ['name', 'target']
    default_permissions = 0

    def __init__(self, name, target):
        self.target = target
        super().__init__(name)

    def create(self, base_dir):
        full_path = base_dir / self.name
        full_path.symlink_to(self.target)

    def __eq__(self, other):
        return (isinstance(other, Symlink)
                and other.name == self.name
                and other.target == self.target)


class PeekableIterator:
    def __init__(self, it):
        self.it = iter(it)
        self.has_top = False
        self.top = None

    def peek(self):
        if not self.has_top:
            self.top = next(self.it)
            self.has_top = True
        return self.top

    def __next__(self):
        if self.has_top:
            self.has_top = False
            self.top = None
            return self.top
        return next(self.it)


class OutputFormatError(Exception):
    pass


def tabs_to_levels(lines):
    linum = 1
    for line in lines:
        line = line.rstrip('\n')
        tabs = 0
        while line.startswith('\t'):
            line = line[1:]
            tabs += 1
        yield linum, tabs, line
        linum += 1


def levels_to_tree(levels):
    def _rec(p_levels, depth):
        while True:
            try:
                linum, tabs, text = p_levels.peek()
            except StopIteration:
                return
            if tabs < depth:
                return
            if tabs == depth:
                children = None
                next(p_levels)
                try:
                    _, next_tabs, _ = p_levels.peek()
                except StopIteration:
                    pass
                else:
                    if next_tabs == tabs + 1:
                        children = list(_rec(p_levels, depth + 1))
                yield text, children
            else:
                raise OutputFormatError(
                    'Line {} of output: too many tabs! (expected at most {} tabs)'.format(
                        linum, depth + 1))

    yield from _rec(PeekableIterator(levels), 0)


def upgrade_tree(basic_tree):
    def _rec(elem):
        name, children = elem
        if children is not None:
            return Dir(name, *map(_rec, children))
        m = re.fullmatch(r'SYM (.*) -> (.*)', name)
        if m:
            return Symlink(m.group(1), m.group(2))
        return FileOrEmptyDir(name)
    return RootDir(*map(_rec, basic_tree))


filename_chars = set(string.printable)
filename_chars.remove('/')
filename_chars.remove('\t')
filename_chars.remove('\n')
filename_chars.remove('\r')
filename_chars.remove('\v')
filename_chars.remove('\x0c')

st_random_contents = st.text(min_size=0, max_size=1 << 16)
st_filename = st.text(
    alphabet=filename_chars,
    min_size=1,
    max_size=254).filter(lambda x: x not in ('.', '..'))
st_relative_path = st.builds(
    lambda parts: '/'.join(parts),
    st.iterables(
        st.one_of(st_filename, st.just('.'), st.just('..')),
        min_size=1,
        max_size=16))
st_absolute_path = st.builds(
    lambda path: '/{}'.format(path),
    st_relative_path)
st_path = st.one_of(st_relative_path, st_absolute_path)

st_file = st.builds(File, st_filename, st_random_contents)
st_symlink = st.builds(Symlink, st_filename, st_path)
st_dir = st.deferred(
    lambda: st.builds(
        lambda name, entries: Dir(name, *entries),
        st_filename,
        st.iterables(
            st.one_of(st_dir, st_file, st_symlink),
            unique_by=lambda f: f.name)))
st_rootdir = st.builds(lambda d: RootDir(*d.entries),
                       st_dir).filter(lambda d: d.max_path_length() <= 4000)

st_dir_no_symlinks = st.deferred(
    lambda: st.builds(
        lambda name, entries: Dir(name, *entries),
        st_filename,
        st.iterables(
            st.one_of(st_dir_no_symlinks, st_file),
            unique_by=lambda f: f.name)))
st_rootdir_no_symlinks = st.builds(lambda d: RootDir(*d.entries),
                                   st_dir_no_symlinks).filter(
                                       lambda d: d.max_path_length() <= 4000)

st_rootdir_only_files = st.builds(
    lambda entries: RootDir(*entries),
    st.iterables(st_file, unique_by=lambda f: f.name))

st_rootdir_only_files_and_symlinks = st.builds(
    lambda entries: RootDir(*entries),
    st.iterables(st.one_of(st_file, st_symlink), unique_by=lambda f: f.name))

st_dir_with_opendir_error = st.builds(
    Dir, st_filename, permissions=st.just(0o300))
st_dir_maybe_with_errors = st.deferred(
    lambda: st.builds(
        lambda name, entries: Dir(name, *entries),
        st_filename,
        st.iterables(
            st.one_of(st_file, st_symlink, st_dir_with_opendir_error,
                      st_dir_maybe_with_errors),
            min_size=2,
            max_size=10,
            unique_by=lambda f: f.name)))
st_rootdir_with_errors = st.builds(lambda d: RootDir(*d.entries),
                                   st_dir_maybe_with_errors).filter(
                                       lambda d: d.count_expected_errors() > 0)

st_random_system_path = st.one_of(
    st.just('/'),
    st.just('/etc'),
    st.just('/usr'),
    st.just('/tmp'),
    st.just('/usr/lib'),
    st.just('/usr/bin'))


def run_tree(args, cwd=None, expected_errors=0):
    args = [os.environ['PROGRAM_PATH']] + args
    if 'WRAP_EXEC' in os.environ:
        args = [os.environ['WRAP_EXEC']] + args
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            encoding='utf-8', env={})
    errors = result.stderr.splitlines()
    if expected_errors:
        assert result.returncode != 0
        assert expected_errors == len(errors)
        return None, errors
    else:
        assert result.returncode == 0
        assert len(errors) == 0
    output = result.stdout
    levels = list(tabs_to_levels(output.splitlines()))
    try:
        tree = list(levels_to_tree(levels))
    except OutputFormatError as e:
        raise OutputFormatError(f'''There was an error counting the tabs in your output:
    {e}

The output of your program is given below, for reference:
{output}''') from e
    try:
        upgraded_tree = upgrade_tree(tree)
    except ValueError as e:
        raise OutputFormatError(f'''There is a semantical issue in the files of your tree:
    {e}

The output of your program is given below, for reference:
{output}''') from e
    return upgraded_tree, errors


@given(st_rootdir_only_files)
def test_no_args_no_symlinks_no_recursion(case):
    with case.tempdir() as d:
        tree, errors = run_tree([], cwd=d)
    assert case == tree


@given(st_rootdir_only_files_and_symlinks)
def test_no_args_no_recursion(case):
    with case.tempdir() as d:
        tree, errors = run_tree([], cwd=d)
    assert case == tree


@given(st_rootdir_no_symlinks)
def test_no_args_no_symlinks(case):
    with case.tempdir() as d:
        tree, errors = run_tree([], cwd=d)
    assert case == tree


@given(st_rootdir)
@example(RootDir(
    File('a.tar.bz2'),
    Dir('projects',
        Symlink('teleportation', '/tmp/foo/bar'),
        File('TOP_SECRET')),
    Dir('bar',
        File('fib.c'),
        File('prog.c'),
        Dir('baz',
            Dir('bip', File('zip.txt')),
            Dir('bam', File('b.files'), File('a.out'))))))
def test_no_args(case):
    with case.tempdir() as d:
        tree, errors = run_tree([], cwd=d)
    assert case == tree


@given(st_rootdir)
def test_one_arg_absolute_and_cwd(case):
    with case.tempdir() as d:
        p = pathlib.Path(d).resolve()
        tree, errors = run_tree([p], cwd=p)
    assert case == tree


@given(st_rootdir)
def test_one_arg_relative_cwd_is_parent(case):
    with case.tempdir() as d:
        p = pathlib.Path(d)
        tree, errors = run_tree([p.name], cwd=p.parent)
    assert case == tree


@given(st_rootdir, st_random_system_path)
def test_one_arg_absolute_cwd_is_random(case, random_path):
    with case.tempdir() as d:
        p = pathlib.Path(d).resolve()
        tree, errors = run_tree([p], cwd=random_path)
    assert case == tree


@given(st_rootdir_with_errors)
def test_dir_with_errors(case):
    with case.tempdir() as d:
        run_tree([], cwd=d, expected_errors=case.count_expected_errors())


def test_dir_does_not_exist():
    run_tree(['/this/path/does/not/exist'], cwd='/', expected_errors=1)


def test_dir_is_file():
    f = File('foo.txt', 'bar\n', permissions=0o700)
    case = RootDir(f)
    with case.tempdir() as d:
        run_tree([f.name], cwd=d, expected_errors=1)


def main(argv):
    parser = argparse.ArgumentParser(
        description='Integration tests for the project.',
        epilog='Any extra arguments will be passed to pytest. See "pytest --help".',
    )
    parser.add_argument(
        '-c', '--compile',
        action='store_true',
        help='Run "make" in the directory of the program before starting tests.',
    )
    parser.add_argument(
        '-p', '--program',
        type=pathlib.Path,
        default=pathlib.Path(__file__).parent / 'tree',
        help='Path to the tree program to test. Defaults to "tree" in the directory next to this file.',
    )
    parser.add_argument(
        '--wrap-exec',
        type=pathlib.Path,
        help='Wrap all executions of the program to test with this binary.  '
        'This could be useful, for example, to jail the program.')
    parser.add_argument(
        '-k', '--keep-tempfiles',
        action='store_true',
        help='Keep all temporary directories created.')
    opts, pytest_args = parser.parse_known_args(argv)
    program_path = opts.program.resolve()

    if opts.compile:
        try:
            subprocess.run(['make'], check=True, cwd=program_path.parent)
        except subprocess.CalledProcessError:
            print('Compile failed!', file=sys.stderr)
            return 1

    if not program_path.is_file():
        print('The program {} does not exist.'.format(program_path),
              file=sys.stderr)
        if opts.compile:
            print('Note: "make" ran and succeeded.  Perhaps the name of the file created is incorrect?',
                  file=sys.stderr)
        else:
            print('Did you remember to run "make" first?', file=sys.stderr)
            print('Hint: try passing --compile and I will run "make" for you.', file=sys.stderr)
        return 1

    pytest_args.append('--')
    pytest_args.append(__file__)

    env = {'PROGRAM_PATH': str(program_path)}
    if opts.keep_tempfiles:
        env['KEEP_TEMPFILES'] = '1'
    if opts.wrap_exec:
        env['WRAP_EXEC'] = str(opts.wrap_exec.resolve())
    proc = subprocess.run([sys.executable, '-m', 'pytest'] + pytest_args,
                          env=env)
    return proc.returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
