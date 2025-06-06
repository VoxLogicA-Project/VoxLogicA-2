# HMR (Hot Module Reload) Debugging Issue

## Problem

The HMR feature in VoxLogicA serve was not working properly. User reported that file changes were not triggering browser reloads.

## Root Causes Identified

1. **File watcher missing error handling**: The `ReloadEventHandler.on_any_event` method didn't handle WebSocket exceptions properly
2. **Missing logging**: No logging to indicate when file changes were detected or when reload signals were sent
3. **Observer reference not kept**: The file watcher observer wasn't being kept as a reference, potentially causing garbage collection issues
4. **Directory events not filtered**: The file watcher was responding to directory changes as well as file changes

## Fixes Applied

1. **Enhanced file watcher logging**: Added comprehensive logging in `ReloadEventHandler` to track file changes and reload signals
2. **Improved error handling**: Added try-catch blocks around WebSocket operations with proper cleanup
3. **Fixed observer lifecycle**: Modified `start_file_watcher` to return the observer and properly manage its lifecycle
4. **Filtered directory events**: Added check to ignore directory change events
5. **Console redirection**: Added browser console redirection to server logs via WebSocket

## Implementation Details

- Modified `ReloadEventHandler.on_any_event` to filter directory events and add logging
- Updated `start_file_watcher` to return observer reference and add initialization logging
- Enhanced `serve` command to properly manage observer lifecycle with try-finally block
- Added WebSocket message handling for browser console logs (log, error, warn)
- Browser console now sends messages to server via WebSocket JSON protocol

## Testing

- Server restarted with debug logging enabled
- File changes in `static/index.html` should now trigger:
  1. File watcher detection (logged)
  2. WebSocket reload signal (logged)
  3. Browser reload
- Browser console messages now appear in server logs with `[BROWSER]` prefix

## Additional Fixes Applied

After user reported HMR still not working (though console redirection worked):

6. **Fixed asyncio thread integration**: The file watcher runs in a separate thread from the asyncio event loop. Fixed by using `asyncio.run_coroutine_threadsafe()` to properly schedule WebSocket operations in the event loop.
7. **Enhanced WebSocket client management**: Improved error handling and client cleanup in the `_notify_clients` coroutine
8. **Fixed server lifecycle**: Moved file watcher initialization to FastAPI startup/shutdown events to ensure proper event loop access
9. **Added global observer management**: Better lifecycle management of the file watcher observer

## Root Cause of Original Issue

The main issue was that the file watcher was trying to send WebSocket messages from a different thread without proper asyncio integration. The `create_task()` call was not being executed in the correct event loop context.

## Status

- **Fixed**: File watcher implementation completely overhauled with proper asyncio integration
- **Fixed**: Console redirection confirmed working by user
- **Testing**: HMR should now work properly - file changes should trigger browser reload
- **Added**: Comprehensive logging for debugging file watcher behavior
