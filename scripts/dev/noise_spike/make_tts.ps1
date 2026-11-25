# Synthetic test speech for the noisy-room endpointing spike.
#
# Writes 16 kHz mono WAVs with the Windows built-in voices (System.Speech):
#   <out>\cmd\*.wav     the commands, one per file (one commander voice each)
#   <out>\wake\*.wav    the wake phrase, per voice
#   <out>\babble\*.wav  long runs of party chatter, one per talker
# Everything is synthesized here: no recorded or downloaded audio. The
# harness (endpoint_harness.py) turns the babble files into multi-talker
# babble with pitch and gain changes, and mixes it with the commands.
#
#   powershell -File scripts/dev/noise_spike/make_tts.ps1 -Out <folder>
param([Parameter(Mandatory = $true)][string]$Out)

Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$voices = (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }

function Save-Speech([string]$voice, [int]$rate, [string]$text, [string]$path) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice($voice)
    $s.Rate = $rate
    $s.SetOutputToWaveFile($path, $fmt)
    $s.Speak($text)
    $s.Dispose()
}

foreach ($d in 'cmd', 'wake', 'babble') { New-Item -ItemType Directory -Force (Join-Path $Out $d) | Out-Null }

$commands = @(
    'Play some jazz in the kitchen.',
    'Set a timer for ten minutes.',
    'Turn the volume down.',
    'What is the weather tomorrow?',
    'Skip this song.',
    'Add milk and eggs to the shopping list.',
    'Pause the music.',
    'Play the next episode of my podcast.',
    'Turn off the lights in the living room.',
    'How long until the pizza is ready?'
)
$babble = @(
    'So we finally drove up to the lake last weekend and honestly the weather was perfect the whole time.',
    'No, she said the new place has a much bigger kitchen, but the commute is going to be terrible.',
    'Did you try the dip over there? I think it has roasted peppers in it, it is really good.',
    'He has been working on that car for three years and I do not think it has ever actually started.',
    'We should all go to that concert in the spring, tickets go on sale next Friday I think.',
    'My sister just got a puppy and now every single photo she sends me is of the dog.',
    'I told him the meeting was at nine, and of course he showed up at ten thirty with coffee.',
    'The kids have a soccer tournament out of town, so the whole weekend is basically gone.',
    'Honestly I would rather stay in, but everyone said this party was going to be fun.',
    'Wait, are you the one who painted the mural downtown? That is amazing, how long did it take?',
    'We tried that restaurant on the corner, the one with the blue sign, and the noodles were great.',
    'I keep meaning to fix the fence in the back yard but it has rained every weekend this month.'
)

$i = 0
foreach ($v in $voices) {
    $tag = ($v -split ' ')[1].ToLower()
    Save-Speech $v 0 'Hey Domovoi.' (Join-Path $Out "wake\$tag.wav")
    foreach ($c in $commands) {
        Save-Speech $v 0 $c (Join-Path $Out ("cmd\{0}_{1:d2}.wav" -f $tag, $i)); $i++
    }
    foreach ($r in -2, 0, 2) {
        $shuffled = $babble | Sort-Object { Get-Random }
        Save-Speech $v $r ($shuffled -join ' ') (Join-Path $Out ("babble\{0}_r{1}.wav" -f $tag, $r))
    }
}
Write-Output "voices: $($voices -join ', ')"
