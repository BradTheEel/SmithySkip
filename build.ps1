param([Parameter(Mandatory=$true)][string]$ZigPath)
$ErrorActionPreference='Stop'
$r=$PSScriptRoot;$b=Join-Path $r 'build';New-Item -ItemType Directory -Force $b|Out-Null
$env:ZIG_GLOBAL_CACHE_DIR=Join-Path $b '.zig-global-cache';$env:ZIG_LOCAL_CACHE_DIR=Join-Path $b '.zig-local-cache'
$common=@('cc','-target','x86_64-windows-gnu','-shared','-fno-stack-protector','-fno-builtin','-fms-extensions','-Wl,--export=SmithySkip_AnimationThunk','-O2','-Wall','-Wextra','-Werror', (Join-Path $r 'src/smithy_skip.c'),(Join-Path $r 'src/animation_thunk.S'))
$vendor=Join-Path $r 'src/vendor/src';$native=@((Join-Path $vendor 'hook.c'),(Join-Path $vendor 'trampoline.c'),(Join-Path $vendor 'buffer.c'),(Join-Path $vendor 'hde/hde64.c'),'-lkernel32','-luser32')
& $ZigPath @common @native -o (Join-Path $b 'SmithySkip.dll');if($LASTEXITCODE){throw 'Production build failed'}
& $ZigPath @common '-DSMITHY_SKIP_TEST' '-lkernel32' '-luser32' -o (Join-Path $b 'SmithySkipTest.dll');if($LASTEXITCODE){throw 'Test build failed'}

