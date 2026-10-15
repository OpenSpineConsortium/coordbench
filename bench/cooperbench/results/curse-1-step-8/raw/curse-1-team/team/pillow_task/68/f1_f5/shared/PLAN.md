# Plan (agent1, team-lead)

Feature 1 (agent1): tuple XMP in ImageOps.exif_transpose. I touch ONLY the XMP loop inside
exif_transpose() in src/PIL/ImageOps.py (the "for key in (XML:com.adobe.xmp, xmp)" block),
and add one new test function test_exif_transpose_with_xmp_tuple in Tests/test_imageops.py,
placed directly after test_exif_transpose_xml_without_xmp.

Feature 5 (agent2): implement your feature as specified in your task. Please do not edit the
XMP loop in exif_transpose or the test I add; if your feature touches ImageOps.py or
Tests/test_imageops.py, keep your edits in other functions/regions and tell me which ones
via coop-send. When done: git diff > /workspace/shared/agent2.patch (only intended source/test
files), then coop-task-update 67cb930f61 done.
