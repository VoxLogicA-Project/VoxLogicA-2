# Issue: Suppress Dask Worker Debug Messages in Production

**Date:** 25 giugno 2025  
**Status:** CLOSED  
**Priority:** Medium  

## Problem Description

VoxLogicA was displaying internal Dask worker management messages in production output that should only appear in debug mode:

```
[     512ms] Removing worker 'inproc://192.168.8.138/88270/4' caused the cluster to lose already computed task(s), which will be recomputed elsewhere: {'finalize-hlgfinalizecompute-8bd49ce19db9429aaf1a6fe8225dd96f'} (stimulus_id='handle-worker-cleanup-1750826037.232414')
```

These messages are internal diagnostics from the Dask distributed scheduler and should not be visible to end users during normal operation.

## Root Cause

The logging configuration in `implementation/python/voxlogica/main.py` was not sufficiently suppressing all Dask distributed scheduler messages. While most Dask loggers were set to WARNING level in production mode, the specific scheduler logger that handles worker cleanup messages was not being set to a high enough level.

## Solution Implemented

Enhanced the logging configuration with:

1. **Extended Dask Logger List**: Added more specific distributed loggers to the suppression list:
   - `distributed.batched`
   - `distributed.comm.core` 
   - `distributed.comm.inproc`
   - `distributed.deploy.local`
   - `distributed.deploy.spec`
   - `distributed.worker_memory`
   - `distributed.stealing`
   - `distributed.shuffle`

2. **Scheduler-Specific Suppression**: Added explicit suppression for the distributed scheduler logger:
   ```python
   # Specifically suppress scheduler messages about worker removal  
   if not debug:
       scheduler_logger = logging.getLogger('distributed.scheduler')
       scheduler_logger.setLevel(logging.CRITICAL)  # Only show critical errors
   ```

## Files Modified

- `implementation/python/voxlogica/main.py` - Enhanced logging configuration in `setup_logging()` function

## Testing

- **Before Fix**: Running `./voxlogica run test_simpleitk.imgql` showed the problematic debug message
- **After Fix**: Same command produces clean output with no internal Dask messages
- **Debug Mode**: Debug messages still appear when `--debug` flag is used (preserved functionality)

## Impact

- ✅ Production output is now clean and user-friendly
- ✅ Debug functionality is preserved when explicitly requested
- ✅ No performance impact
- ✅ No changes to API or CLI interface

## Related Files

- Test file: `test_simpleitk.imgql` (restored from git history)
- Main implementation: `implementation/python/voxlogica/main.py`
