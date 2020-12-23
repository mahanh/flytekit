import os
import pathlib
import shutil

import flytekit
from flytekit.annotated import context_manager
from flytekit.annotated.task import task
from flytekit.annotated.type_engine import TypeEngine
from flytekit.annotated.workflow import workflow
from flytekit.interfaces.data.data_proxy import FileAccessProvider
from flytekit.models.core.types import BlobType
from flytekit.types.flyte_directory import FlyteDirectory, FlyteDirectoryTransformer


def test_engine():
    t = FlyteDirectory
    lt = TypeEngine.to_literal_type(t)
    assert lt.blob is not None
    assert lt.blob.dimensionality == BlobType.BlobDimensionality.MULTIPART
    assert lt.blob.format == ""

    t2 = FlyteDirectory["csv"]
    lt = TypeEngine.to_literal_type(t2)
    assert lt.blob is not None
    assert lt.blob.dimensionality == BlobType.BlobDimensionality.MULTIPART
    assert lt.blob.format == "csv"


def test_transformer_to_literal_local():
    random_dir = context_manager.FlyteContext.current_context().file_access.get_random_local_directory()
    fs = FileAccessProvider(local_sandbox_dir=random_dir)
    with context_manager.FlyteContext.current_context().new_file_access_context(file_access_provider=fs) as ctx:
        # Use a separate directory that we know won't be the same as anything generated by flytekit itself, lest we
        # accidentally try to cp -R /some/folder /some/folder/sub which causes exceptions obviously.
        p = "/tmp/flyte/test_fd_transformer"

        # Create an empty directory and call to literal on it
        if os.path.exists(p):
            shutil.rmtree(p)
        pathlib.Path(p).mkdir(parents=True)

        tf = FlyteDirectoryTransformer()
        lt = tf.get_literal_type(FlyteDirectory)
        literal = tf.to_literal(ctx, FlyteDirectory(p), FlyteDirectory, lt)
        assert literal.scalar.blob.uri.startswith(random_dir)

        # Create a director with one file in it
        if os.path.exists(p):
            shutil.rmtree(p)
        pathlib.Path(p).mkdir(parents=True)
        with open(os.path.join(p, "xyz"), "w") as fh:
            fh.write("Hello world\n")
        literal = tf.to_literal(ctx, FlyteDirectory(p), FlyteDirectory, lt)

        mock_remote_files = os.listdir(literal.scalar.blob.uri)
        assert mock_remote_files == ["xyz"]


def test_transformer_to_literal():
    random_dir = context_manager.FlyteContext.current_context().file_access.get_random_local_directory()
    fs = FileAccessProvider(local_sandbox_dir=random_dir)
    with context_manager.FlyteContext.current_context().new_file_access_context(file_access_provider=fs) as ctx:
        # Use a separate directory that we know won't be the same as anything generated by flytekit itself, lest we
        # accidentally try to cp -R /some/folder /some/folder/sub which causes exceptions obviously.
        p = "/tmp/flyte/test_fd_transformer"
        # Create an empty directory and call to literal on it
        if os.path.exists(p):
            shutil.rmtree(p)
        pathlib.Path(p).mkdir(parents=True)

        tf = FlyteDirectoryTransformer()
        lt = tf.get_literal_type(FlyteDirectory)

        # Remote directories should be copied as is.
        literal = tf.to_literal(ctx, FlyteDirectory("s3://anything"), FlyteDirectory, lt)
        assert literal.scalar.blob.uri == "s3://anything"


def test_wf():
    @task
    def t1() -> FlyteDirectory:
        user_ctx = flytekit.current_context()
        # Create a local directory to work with
        p = os.path.join(user_ctx.working_directory, "test_wf")
        if os.path.exists(p):
            shutil.rmtree(p)
        pathlib.Path(p).mkdir(parents=True)
        for i in range(1, 6):
            with open(os.path.join(p, f"{i}.txt"), "w") as fh:
                fh.write(f"I'm file {i}\n")

        return FlyteDirectory(p)

    d = t1()
    files = os.listdir(d.path)
    assert len(files) == 5

    @workflow
    def my_wf() -> FlyteDirectory:
        return t1()

    wfd = my_wf()
    files = os.listdir(wfd.path)
    assert len(files) == 5
