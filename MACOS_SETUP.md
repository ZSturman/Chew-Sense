# macOS Support for ChewSense

ChewSense can run on macOS via Mac Catalyst, allowing you to test and debug
the chewing detection system directly on your Mac while wearing AirPods.

## Requirements

- **macOS 14.0+** (Sonoma or later) - Required for `CMHeadphoneMotionManager`
- **Xcode 15.0+**
- **AirPods Pro, AirPods Max, or AirPods 4** - Connected to your Mac

## Enabling Mac Catalyst

1. **Open the Xcode project:**
   ```
   open ChewSenseDebuggingModel.xcodeproj
   ```

2. **Select the target:**
   - Click on the project in the Navigator
   - Select "ChewSenseDebuggingModel" target

3. **Enable Mac Catalyst:**
   - Go to the **General** tab
   - Under **Supported Destinations**, click the **+** button
   - Select **Mac (Mac Catalyst)**
   
   Alternatively, in the **Build Settings** tab:
   - Search for `SUPPORTS_MACCATALYST`
   - Set it to `YES` for both Debug and Release

4. **Set macOS Deployment Target:**
   - In **Build Settings**, find `MACOSX_DEPLOYMENT_TARGET`
   - Set to `14.0` or later

5. **Build and Run:**
   - Select "My Mac (Mac Catalyst)" from the destination dropdown
   - Build and run (⌘R)

## Platform-Specific Code

The app includes platform conditionals for iOS-only APIs:

### ShareSheet
- **iOS:** Uses `UIActivityViewController`
- **macOS:** Uses "Open in Finder" button

### Motion Manager
- `CMHeadphoneMotionManager` works on both platforms when AirPods are connected
- No platform-specific code needed for motion handling

## Troubleshooting

### "Headphone motion not available"
- Ensure AirPods are connected to your Mac (not just paired)
- Check System Preferences → Sound → Input shows AirPods
- Try disconnecting and reconnecting AirPods

### Build errors with UIKit
If you see errors about `UIActivityViewController` or other UIKit APIs:
- Ensure `ShareSheet` uses `#if os(iOS)` / `#elseif os(macOS)` guards
- Check [MainContentView.swift](ChewSenseDebuggingModel/Views/MainContentView.swift) for reference

### App crashes on launch
- Verify macOS deployment target is 14.0+
- Check that CoreML model is included in target

## Testing Notes

- macOS Catalyst apps run in a simulated iOS environment
- UI may look slightly different than native macOS
- Performance should be equivalent to iOS

## Files with Platform Conditionals

| File | Conditional | Reason |
|------|-------------|--------|
| [MainContentView.swift](ChewSenseDebuggingModel/Views/MainContentView.swift) | `#if os(iOS)` / `#elseif os(macOS)` | ShareSheet implementation differs |
| [MotionManager.swift](ChewSenseDebuggingModel/Managers/MotionManager.swift) | None | CMHeadphoneMotionManager works on both |

## Hardware Compatibility

| Device | macOS Support |
|--------|---------------|
| AirPods Pro (1st/2nd gen) | ✅ Yes |
| AirPods Max | ✅ Yes |
| AirPods (4th gen) | ✅ Yes |
| AirPods (3rd gen and earlier) | ❌ No motion sensors |
| Other Bluetooth headphones | ❌ No |

## Known Console Noise on Mac Catalyst

The following log lines are emitted by the **system frameworks** that host
`AVCaptureVideoPreviewLayer` and `AVPlayerViewController` on Mac Catalyst,
not by ChewSense code. They are harmless and can be ignored:

- `[ParticleQuadRenderer] couldn't remap entity: <Entity[…,-1]>`
- `[GraphScript] couldn't remap entity: <Entity[…,-1]>`
- `[PropagateDirtiness] couldn't remap entity: <Entity[…,-1]>`
- `<VFXNode: 0x… 'emitter_SpriteFG' | no child>`
- `<<<< FigAirPlay_Route >>>> signalled err=-12860 …`
- `<<<< VRP >>>> signalled err=-12852 …`
- `-[CIImage initWithCGImage:options:] failed because the CGImage is nil.`
- `cannot add handler to 4 from 1 - dropping`

These come from RealityKit / AirPlay subsystems that AVKit instantiates
internally. The project ships zero RealityKit / particle / `.reality` /
`.usdz` assets, so there is nothing in the app to silence them.

If you encounter `<<<< FigFilePlayer >>>> signalled err=-12860` while
reviewing a recording, inspect the `.mov` directly with `ffprobe` — the
fragment-interval setting in `DebugVideoRecorder` keeps the moov atom
fresh, so partial files should still be playable.
