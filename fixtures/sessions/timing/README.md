# Synthetic presentation-timestamp fixture

`leading-empty-edit.mov` is a 16×16 black H.264 movie made locally with
AVAssetWriter. It contains no participant recording or audio. Writer source
origin: 100000 seconds. Appended frame host times: 100000.3, 100000.333333,
100000.4. The first 300 ms are an empty movie edit.

Expected display PTS: 0.3, 0.333333, 0.4 seconds. Movie end: approximately
0.466667 seconds. Compressed AVAssetReader output can contain media-local
PTS and empty-edit marker buffers; those are not the display timeline.

`ChewSenseCollectTests.hostClockPresentationTimestampSurvivesWriterExport`
recreates the native writer/edit-mapping check. The Python timing test checks
this fixture through ffprobe and OpenCV without a nominal-FPS fallback.
