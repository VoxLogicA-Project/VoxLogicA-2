"""`pinned_view` is a WRITE, and concurrent readers were racing inside ITK.

``GetArrayViewFromImage`` calls ``image.MakeUnique()``, which detaches
SimpleITK's copy-on-write buffer and reassigns the internal smart pointer.
Kernels call it straight on their inputs, and an input is a shared value every
worker that needs it reads at the same instant. Without the GIL nothing
serialised that, and it killed two 369-case runs in one day with the same
stack: SIGSEGV inside MakeUnique, then glibc's `corrupted size vs prev_size`.

These pin the fix: one construction per image, under a lock, cached weakly.
"""

import threading

import numpy as np
import pytest

from voxlogica.arrays import pinned_view
from voxlogica.buffer_pool import VIEW_ATTR, acquire_sitk, reset_pool_for_tests

sitk = pytest.importorskip("SimpleITK")


@pytest.fixture
def image():
    img = sitk.Image((8, 8, 4), sitk.sitkFloat32)
    img[0, 0, 0] = 3.5
    return img


@pytest.mark.unit
def test_the_view_is_built_once_and_reused(image):
    """A repeat reader must not re-enter MakeUnique."""
    first = pinned_view(image)
    assert pinned_view(image) is first


@pytest.mark.unit
def test_the_view_still_pins_its_image(image):
    """Caching must not cost the property the function exists for."""
    view = pinned_view(image)
    assert view._src is image


@pytest.mark.unit
def test_concurrent_readers_all_get_the_same_view(image):
    """The shape that crashed: many workers reading one shared input at once."""
    views = []
    barrier = threading.Barrier(8)

    def read():
        barrier.wait()          # maximise the overlap
        views.append(pinned_view(image))

    threads = [threading.Thread(target=read) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(views) == 8
    assert all(v is views[0] for v in views), "one construction, one view"
    assert float(views[0].reshape(-1)[0]) == pytest.approx(3.5)


@pytest.mark.unit
def test_the_cache_is_weak_so_the_image_can_still_die(image):
    """A strong cache would close image -> view -> _src -> image.

    That cycle would take every volume out of refcount reclamation and leave it
    to the collector, which is exactly the memory behaviour the engine's budget
    cannot see.
    """
    view = pinned_view(image)
    ref = getattr(image, VIEW_ATTR)
    assert ref() is view
    del view
    assert ref() is None, "nothing but the caller should keep the view alive"


@pytest.mark.unit
def test_a_recycled_image_does_not_carry_its_previous_view():
    """The pool reuses the image OBJECT, so identity-keyed state must be cleared.

    Otherwise a view built during the buffer's previous life stays reachable
    from it and describes a value that no longer exists there.
    """
    reset_pool_for_tests()
    reference = sitk.Image((4, 4, 2), sitk.sitkFloat32)
    try:
        first = acquire_sitk(reference, sitk.sitkFloat32)
        view = pinned_view(first)
        assert getattr(first, VIEW_ATTR, None) is not None
        # Hand it back the way the engine does, then take it out again.
        from voxlogica.buffer_pool import recycle_unleased_states, buffer_states
        del view
        recycle_unleased_states(buffer_states(first))
        del first
        again = acquire_sitk(reference, sitk.sitkFloat32)
        assert getattr(again, VIEW_ATTR, None) is None
    finally:
        reset_pool_for_tests()


@pytest.mark.unit
def test_the_view_reads_the_image_correctly(image):
    """Cheap correctness net: the fast path must still be the right numbers."""
    expected = sitk.GetArrayFromImage(image)
    assert np.array_equal(np.asarray(pinned_view(image)), expected)
