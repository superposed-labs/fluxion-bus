import AppKit
import Foundation
import Sparkle
import UserNotifications

/// Wraps Sparkle so update behavior stays deliberate:
///
///   • Auto-update only runs in official distribution builds. A build without
///     an `SUFeedURL` in its Info.plist (a plain `desktop/build.sh` dev or
///     self-compiled build) leaves the updater inert — it never phones home
///     and never offers to replace the user's own build. Distribution builds
///     inject `SUFeedURL` (see `desktop/build.sh` + `package-macos-app.sh`).
///   • Updates are never installed silently — the user always confirms.
///   • Scheduled reminders are gentle: when the app is not in focus, a found
///     update posts a non-modal notification instead of stealing focus with a
///     modal window.
///
/// Delegate methods use explicit `@objc(...)` selectors: these are optional
/// protocol methods, and a mismatched Swift name would compile cleanly yet
/// silently never be called.
final class UpdaterController: NSObject {
    /// nil when no `SUFeedURL` is configured (dev / self-compiled build).
    private(set) var controller: SPUStandardUpdaterController?

    /// True while a scheduled update is waiting for the user to act on it.
    private(set) var hasPendingUpdate = false

    /// Fired whenever `hasPendingUpdate` changes so the UI can reflect it.
    var onPendingUpdateChange: (() -> Void)?

    /// Whether this build has auto-update wired at all (a distribution build).
    var isConfigured: Bool { controller != nil }

    override init() {
        super.init()
        // Only wire Sparkle when a feed is present. Distribution builds inject
        // SUFeedURL; dev / self-compiled builds ship without it and stay inert.
        guard Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil else {
            return
        }
        let controller = SPUStandardUpdaterController(
            startingUpdater: true, updaterDelegate: self, userDriverDelegate: self)
        // Default off: never download/install without explicit confirmation.
        controller.updater.automaticallyDownloadsUpdates = false
        self.controller = controller
    }

    /// Background check on/off (the Welcome + Preferences toggle binds here).
    var automaticallyChecksForUpdates: Bool {
        get { controller?.updater.automaticallyChecksForUpdates ?? false }
        set { controller?.updater.automaticallyChecksForUpdates = newValue }
    }

    var canCheckForUpdates: Bool { controller?.updater.canCheckForUpdates ?? false }

    /// User-initiated check. Sparkle presents its full UI (progress, the
    /// "up to date" notice, or the install prompt).
    func checkForUpdates() {
        controller?.checkForUpdates(nil)
    }

    private func setPending(_ pending: Bool) {
        guard hasPendingUpdate != pending else { return }
        hasPendingUpdate = pending
        onPendingUpdateChange?()
    }

    private func postGentleNotification(for update: SUAppcastItem) {
        let content = UNMutableNotificationContent()
        content.title = L10n.tr("update.available.title")
        content.body = L10n.tr("update.available.body", update.displayVersionString)
        let request = UNNotificationRequest(
            identifier: "fluxion.update.available", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }
}

extension UpdaterController: SPUUpdaterDelegate {}

extension UpdaterController: SPUStandardUserDriverDelegate {
    var supportsGentleScheduledUpdateReminders: Bool { true }

    @objc(standardUserDriverShouldHandleShowingScheduledUpdate:andInImmediateFocus:)
    func standardUserDriverShouldHandleShowingScheduledUpdate(
        _ update: SUAppcastItem, andInImmediateFocus immediateFocus: Bool
    ) -> Bool {
        // If the app is already in focus, let Sparkle present its update UI
        // normally. Otherwise (the usual case for a menu-bar app) we handle it
        // gently below so we never steal focus with a modal.
        return immediateFocus
    }

    @objc(standardUserDriverWillHandleShowingUpdate:forUpdate:state:)
    func standardUserDriverWillHandleShowingUpdate(
        _ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem,
        state: SPUUserUpdateState
    ) {
        // Sparkle deferred to us (handleShowingUpdate == false): surface a
        // quiet, non-modal reminder instead.
        guard !handleShowingUpdate else { return }
        setPending(true)
        postGentleNotification(for: update)
    }

    @objc(standardUserDriverDidReceiveUserAttentionForUpdate:)
    func standardUserDriverDidReceiveUserAttention(forUpdate update: SUAppcastItem) {
        setPending(false)
    }

    @objc(standardUserDriverWillFinishUpdateSession)
    func standardUserDriverWillFinishUpdateSession() {
        setPending(false)
    }
}
