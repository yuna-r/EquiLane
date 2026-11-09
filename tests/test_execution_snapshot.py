"""Lossless publication and the GitHub Pages copy boundary."""
from __future__ import annotations
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('execution_snapshot',ROOT/'tools/export_execution_snapshot.py')
module=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(module)


class ExecutionSnapshotTests(unittest.TestCase):
    def fixture(self,root):
        report=root/'report';report.mkdir()
        raw=root/'source.jsonl';raw.write_bytes(b'{"value":"0x80000000"}\n{"value":"0x7fc00000"}\n')
        metadata=module.compress(raw,report/'data/records.jsonl.gz',records=2)
        (report/'index.html').write_text('<html lang="ja"><h1>Recorded report</h1></html>')
        (report/'index.en.html').write_text('<html lang="en"><h1>Recorded report</h1></html>')
        value={'schema_version':1,'artifact_type':'equilane.execution-publication','streams':{'data/records.jsonl.gz':metadata},'files':module.inventory(report)}
        module.write(report/'publication.json',value)
        return report,raw

    def test_archives_are_deterministic_and_preserve_original_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);report,raw=self.fixture(root)
            metadata=module.compress(raw,root/'second.gz',records=2)
            self.assertEqual((report/'data/records.jsonl.gz').read_bytes(),(root/'second.gz').read_bytes())
            self.assertEqual(gzip.decompress((root/'second.gz').read_bytes()),raw.read_bytes())
            self.assertEqual(metadata['uncompressed_sha256'],hashlib.sha256(raw.read_bytes()).hexdigest())
            module.verify_snapshot(report)

    def test_truncated_records_and_archive_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);report,raw=self.fixture(root)
            raw.write_bytes(b'{"value":"0x00"}')
            with self.assertRaises(ValueError):module.compress(raw,root/'bad.gz',records=1)
            archive=report/'data/records.jsonl.gz'
            archive.write_bytes(archive.read_bytes()+b'changed')
            with self.assertRaises(ValueError):module.verify_snapshot(report)

    def test_unlisted_files_and_symlinks_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);report,_=self.fixture(root)
            (report/'private.log').write_text('must not publish')
            with self.assertRaises(ValueError):module.stage_site(report,root/'site')
            self.assertFalse((root/'site').exists())
            (report/'private.log').unlink()
            archive=report/'data/records.jsonl.gz';data=archive.read_bytes();archive.unlink()
            outside=root/'outside.gz';outside.write_bytes(data);archive.symlink_to(outside)
            with self.assertRaises(ValueError):module.verify_snapshot(report)
            for name in ('../outside.gz','/tmp/outside.gz'):
                with self.assertRaises(ValueError):module.safe(report,name)

    def test_site_preserves_existing_routes_and_links_the_readable_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);report,_=self.fixture(root)
            legacy=root/'legacy';(legacy/'arm').mkdir(parents=True)
            (legacy/'index.html').write_text('<html lang="ja"><h1>Earlier canonical report</h1></html>')
            (legacy/'arm/index.html').write_text('<h1>Earlier ARM</h1>')
            (legacy/'private.log').write_text('do not copy')
            module.stage_site(report,root/'site',legacy=legacy)
            self.assertEqual((root/'site/arm/index.html').read_text(),'<h1>Earlier ARM</h1>')
            self.assertTrue((root/'site/extended/index.html').is_file())
            self.assertIn('href="extended/"',(root/'site/index.html').read_text())
            self.assertIn('extended/index.en.html',(root/'site/index.en.html').read_text())
            self.assertFalse((root/'site/private.log').exists())

if __name__=='__main__':unittest.main()
