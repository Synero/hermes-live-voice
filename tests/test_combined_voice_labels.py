"""Integration guards for localized voice labels without changing wire IDs."""
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'desktop/plugin.js'


@pytest.mark.parametrize('locale, hint', [
    ('de-DE', 'regionale Ausrichtung'),
    ('es-ES', 'orientación regional'),
    ('fr-FR', 'regional focus'),
])
def test_voice_labels_keep_language_without_per_option_warning(locale, hint):
    source = SOURCE.read_text()
    # Evaluate the real declarations, without React, networking or microphone access.
    start = source.index('const VOICES = ')
    end = source.index('\n// A POST ', start)
    script = 'const navigator = {language: ' + json.dumps(locale) + '};\n'
    script += source[start:end]
    script += '\nconsole.log(JSON.stringify({label: voiceOptionLabel("miko"), id: effVoiceFor("codex", "miko"), hint: voiceLanguageHint()}));'
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['label'] == 'Miko'
    assert data['id'] == 'miko'
    assert hint in data['hint']


@pytest.mark.parametrize('locale, first_language', [('de-DE', 'Deutsch'), ('es-MX', 'Español (México)'), ('fr-FR', 'English'), ('it-IT', 'English')])
def test_codex_dropdown_groups_by_language_and_sorts_without_changing_voice_ids(locale, first_language):
    source = SOURCE.read_text()
    start = source.index('const VOICES = ')
    end = source.index('\n// A POST ', start)
    script = 'const navigator = {language: ' + json.dumps(locale) + '};\n'
    script += source[start:end]
    script += '\nconsole.log(JSON.stringify({groups: voiceGroupsFor("codex"), hermes: voiceGroupsFor("hermes")}));'
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    groups = data['groups']
    assert groups[0]['label'] == first_language
    ids = [voice['id'] for group in groups for voice in group['voices']]
    assert len(ids) == 23
    assert len(set(ids)) == 23
    assert set(ids) == {'cove', 'juniper', 'maple', 'spruce', 'ember', 'vale', 'breeze', 'arbor', 'sol', 'aube', 'bubbie', 'haetsal', 'hanul', 'himari', 'leher', 'miko', 'neer', 'rindo', 'rio', 'rivage', 'sonna', 'tinta', 'viola'}
    assert all(voice['label'][0].isupper() for group in groups for voice in group['voices'])
    for group in groups:
        assert group['voices'] == sorted(group['voices'], key=lambda voice: voice['label'])
    assert len(data['hermes']) == 1
    assert [voice['id'] for voice in data['hermes'][0]['voices']] == ['alloy', 'ballad', 'cedar', 'echo', 'marin', 'sage', 'shimmer', 'verse']


@pytest.mark.parametrize('locale, expected', [('de-DE', 'Woche'), ('en-US', 'Weekly'), ('es-ES', 'Semanal')])
def test_backend_weekly_window_label_is_localized(locale, expected):
    source = SOURCE.read_text()
    start = source.index('const VOICES = ')
    end = source.index('\n// A POST ', start)
    script = 'const navigator = {language: ' + json.dumps(locale) + '};\n' + source[start:end]
    script += '\nconsole.log(JSON.stringify([usageWindowLabel("semanal"), usageWindowLabel("5h"), usageWindowLabel("custom")]));'
    result = subprocess.run(['node', '-e', script], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [expected, '5h', 'custom']
