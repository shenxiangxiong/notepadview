import pymupdf as fitz
import pytest

from notepadview.pdf_model import PdfModel


def make_model():
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 65), 'Short', fontsize=16)
        return PdfModel(doc.tobytes())


def test_checkpoint_replay_preserves_accepted_legacy_bounds():
    model = make_model()
    try:
        model.replay_operation({'method': 'replace_text', 'args': [0, 0, 'Short'],
                                'kwargs': {'offset': [-42, 0]}})
        before = model.checkpoint()
        expected = model.text_blocks(0)
        model.rotate_page(0, 90)
        model.restore_checkpoint(before)
        assert model.rotation(0) == 0
        assert model.text_blocks(0) == expected
    finally:
        model.close()


@pytest.mark.parametrize('rect', [(-10, 100, 250, 180), (40, -10, 250, 80),
                                  (310, 100, 500, 180), (40, 310, 250, 400)])
def test_moving_added_text_outside_page_rolls_back(rect):
    model = make_model()
    try:
        model.add_text(0, (40, 100, 250, 180), 'Added')
        before = model.checkpoint()
        with pytest.raises(ValueError, match='页面'):
            model.update_layer(0, rect=rect)
        assert model.checkpoint() == before
        assert model.layers[0]['rect'] == [40, 100, 250, 180]
    finally:
        model.close()


def test_layer_box_is_clamped_without_losing_text():
    model = make_model()
    try:
        model.add_text(0, (40, 100, 250, 180), 'Added')
        model.update_layer(0, rect=(60, 110, 350, 350))
        assert model.layers[0]['rect'] == [60, 110, 300, 300]
        with fitz.open(stream=model.export(), filetype='pdf') as doc:
            assert 'Added' in doc[0].get_text().replace('\xa0', ' ')
    finally:
        model.close()


def test_legacy_layer_outside_page_can_still_restore():
    model = make_model()
    try:
        model.replay_operation({'method': 'add_text', 'args': [0, [-10, 100, 250, 180], 'Legacy'], 'kwargs': {}})
        assert model.layers[0]['rect'][0] == -10
        before = model.checkpoint()
        model.rotate_page(0, 90)
        model.restore_checkpoint(before)
        assert model.layers[0]['rect'][0] == -10
        assert model.rotation(0) == 0
    finally:
        model.close()
