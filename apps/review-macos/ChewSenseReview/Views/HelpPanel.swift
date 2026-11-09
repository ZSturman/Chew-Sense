import SwiftUI

struct HelpPanel: View {
    let onClose: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("How to label chews").font(.headline)
                Spacer()
                Button(action: onClose) { Image(systemName: "xmark") }
                    .buttonStyle(.plain)
            }
            .padding(.horizontal, 14).padding(.vertical, 12)

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    section(title: "How a review goes") {
                        bullet("Up next shows the one task that needs you. Press Start.")
                        bullet("The panel on the right walks you through the steps shown at the top. Click a step to go back to it.")
                        bullet("Suggestions shown: accept, fix or reject each suggested chew, watch the whole recording for missed ones, then Finish review.")
                        bullet("Blind review: suggestions are hidden. Mark every chew you see (none is a valid answer), then Finish review.")
                        bullet("Your decisions save automatically; close the app any time and continue later. Finished reviews are locked in by the Mac.")
                    }

                    section(title: "The basics") {
                        Text("Each chew is one closed → open → closed cycle. Closed start is the rest position immediately before opening, Maximum open is the largest observable jaw opening, and Closed end is the return to rest. Closed end is also the counted chew-event timestamp.")
                    }

                    section(title: "Add a chew") {
                        bullet("Pause where the mouth is open widest and press A (or Add chew in the panel). Then adjust its start and end if needed.")
                        bullet("The timeline starts in Move playhead mode: click or drag to move playback without changing labels. Choose Edit chews before drawing a Closed start → Closed end range; Maximum open starts at the midpoint.")
                        bullet("Or, with no chew selected, press 1 / 2 / 3 to drop Closed start / Maximum open / Closed end markers at the playhead and create a chew.")
                    }

                    section(title: "Adjust an existing chew") {
                        bullet("In Edit chews mode, click a chew block to select it. Delete selected removes a manual addition or rejects a proposal; Undo restores the previous decision.")
                        bullet("Drag a marker to fine-tune: green ▶ Closed start · yellow ◆ Maximum open · red ◀ Closed end.")
                        bullet("With a chew selected, press 1, 2, or 3 to snap that marker to the playhead.")
                        bullet("Hold ⌥ + ←/→ to nudge the last-touched marker by one frame.")
                    }

                    section(title: "Zoom & navigate") {
                        bullet("Press = / − to zoom in/out around the playhead, 0 to fit, F to focus the selected chew.")
                        bullet("Mouse wheel pans the timeline; ⌘ or ⌥ + scroll zooms; ⇧ + scroll pans 4× faster.")
                        bullet("During playback the timeline keeps the playhead in frame; pan with the trackpad while paused.")
                        bullet(". and , jump to the next / previous marker on any chew.")
                        bullet("N / P jump between chews; [ / ] jump to start / end of the selected chew.")
                    }

                    section(title: "Check suggestions") {
                        bullet("The current suggestion loops automatically; the timeline zooms to fit it. Replay (R) starts it over; Play/Pause (Space) pauses or resumes. Turn Loop off to play each clip once and pause at its end.")
                        bullet("Yes (Return) accepts it and moves on. Not a chew rejects it. Can't tell marks it unclear or hidden.")
                        bullet("Fix the timing nudges each marker by a frame, or sets it to the paused video position.")
                        bullet("One suggestion that is really two chews: pause where the mouth closes between them and press Split in two here. Add a chew here (A) adds a missed chew, fitted between its neighbours.")
                        bullet("Hold S, P, or E and tap ← / → to nudge that marker by one frame (⇧ for 5 frames).")
                        bullet("All suggestions lists every one, so you can revisit and change any answer.")
                    }

                    section(title: "Eating segments") {
                        bullet("Blue bands behind the chew row are synchronized coarse-label evidence. Event Review does not mutate them.")
                        bullet("If coarse labels are missing, return to coarse review and rerun vision before verifying event candidates.")
                    }

                    section(title: "Decide & advance") {
                        bullet("Press V to accept the selected chew and jump to the next pending candidate.")
                        bullet("Use Unobservable when the anchors cannot be seen, and Uncertain when the video is ambiguous. Both decisions exclude the cycle from phase supervision.")
                    }

                    section(title: "Delete or revert") {
                        bullet("Press D or Delete to remove the selected chew.")
                        bullet("Right-click a chew → Revert to auto to undo edits and restore the original.")
                    }

                    section(title: "Hotkeys") {
                        hotkeyTable()
                    }
                }
                .padding(14)
            }
        }
        .frame(maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .overlay(alignment: .leading) { Divider() }
    }

    @ViewBuilder
    private func section(title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline).bold()
            content()
                .font(.callout)
                .foregroundStyle(.primary)
        }
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Text("•").foregroundStyle(.secondary)
            Text(text)
        }
    }

    private func hotkeyTable() -> some View {
        let rows: [(String, String)] = [
            ("Space", "Play / pause"),
            ("1 / 2 / 3", "Set Closed start / Maximum open / Closed end"),
            ("Drag on strip", "Scrub; Edit chews mode creates/adjusts events"),
            ("A", "Add chew at playhead (0.4 s wide)"),
            ("D / Delete", "Delete selected chew"),
            ("N / P", "Next / previous chew"),
            (". / ,", "Next / previous marker (any chew)"),
            ("[ / ]", "Jump to selected chew start / end"),
            ("← / →", "Step ± one frame"),
            ("⇧ ← / ⇧ →", "Skip ± 0.25 s"),
            ("⌥ ← / ⌥ →", "Nudge last marker ± one frame"),
            ("S+← / S+→", "Nudge Closed start ± 1 frame (⇧ for 5)"),
            ("P+← / P+→", "Nudge Maximum open ± 1 frame (⇧ for 5)"),
            ("E+← / E+→", "Nudge Closed end ± 1 frame (⇧ for 5)"),
            ("J / L", "−1 s / +1 s"),
            ("< / >", "Slower / faster playback (¼× to 2×)"),
            ("R", "Replay the suggestion (Check suggestions); otherwise toggle loop on selected chew"),
            ("O", "Toggle Raw / Annotated reference video"),
            ("= / − / 0", "Zoom in / out / fit"),
            ("F", "Focus selected chew"),
            ("V", "Accept selected chew & advance"),
            ("⇧V", "Return decision to pending / accept"),
            ("Return", "Yes, it's a chew (Check suggestions)"),
            ("⌘0", "Back to Up next"),
            ("⌘Z / ⇧⌘Z", "Undo / redo"),
            ("⌘S", "Save now (also automatic)"),
            ("⌘1", "Show or hide all recordings"),
            ("⌘ /", "Toggle this help"),
        ]
        return VStack(alignment: .leading, spacing: 4) {
            ForEach(rows, id: \.0) { row in
                HStack(alignment: .top) {
                    Text(row.0)
                        .font(.system(.caption, design: .monospaced))
                        .frame(width: 110, alignment: .leading)
                        .foregroundStyle(.secondary)
                    Text(row.1).font(.caption)
                }
            }
        }
    }
}
