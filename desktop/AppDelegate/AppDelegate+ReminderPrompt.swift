import AppKit
import Foundation
import UserNotifications

// MARK: - Reminder Coverage Prompt
//
// Weekly reset reminders are stored as per-provider AutoPing rules, and the
// welcome window only ever writes rules for the providers that existed at
// first run. An agent installed later therefore reports quota in the menu bar
// while silently having no reminder — the user believes reminders are "on"
// because they switched them on once.
//
// The trigger is a provider appearing for the first time, not merely one
// without a rule: a provider the user switched off by hand also has no rule,
// and questioning a decision they just made would be worse than the gap.
// Enabling is offered rather than inherited — watching a single provider on
// purpose is a legitimate choice.
extension AppDelegate {

    static let reminderPromptCategoryId = "FLUXION_REMINDER_COVERAGE"
    static let reminderPromptEnableActionId = "FLUXION_REMINDER_ENABLE"
    /// Providers seen reporting usage at some point, which is how a genuinely
    /// new one is recognized. App-local bookkeeping rather than a .env key: it
    /// is not configuration and does not belong in a file users hand-edit.
    static let knownProvidersDefaultsKey = "FluxionKnownWatchableProviders"

    /// nil means never recorded — the difference between "no provider has ever
    /// been seen" and "this install predates the bookkeeping", which decides
    /// whether the first check may speak.
    var knownWatchableProviders: Set<String>? {
        get {
            guard let stored = UserDefaults.standard.stringArray(
                forKey: AppDelegate.knownProvidersDefaultsKey) else { return nil }
            return Set(stored)
        }
        set {
            UserDefaults.standard.set(
                (newValue ?? []).sorted(), forKey: AppDelegate.knownProvidersDefaultsKey)
        }
    }

    /// Records the currently watchable providers without offering anything.
    /// Called on the onboarding path, where the welcome window already asked:
    /// without this the agents present at first run would look new afterwards.
    func seedKnownWatchableProviders() {
        updateKnownProviders { _, _ in nil }
    }

    /// Offers weekly reminders for providers that just showed up for the first
    /// time. Safe to call after any availability detection; it decides on its
    /// own whether to say anything.
    func promptForUnwatchedProvidersIfNeeded() {
        guard (envVals["FLUXION_MENU_MACOS_NOTIFY_REFRESH"] ?? "true").lowercased() != "false" else {
            return
        }
        updateKnownProviders { known, watchable in
            // First run with this bookkeeping: everything looks new because
            // nothing was ever recorded, so record and stay quiet.
            guard let known = known else { return nil }
            return watchable.subtracting(known)
        }
    }

    /// Reads what is watchable now, folds it into the known set, and hands the
    /// before/after to `newcomers` to decide what (if anything) to offer.
    ///
    /// The scheduler probe blocks on a subprocess, so the work runs off the
    /// main thread; the defaults and the prompt are touched on it.
    private func updateKnownProviders(
        _ newcomers: @escaping (Set<String>?, Set<String>) -> Set<String>?
    ) {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self = self else { return }
            guard let snapshot = self.loadAvailabilitySnapshot() else { return }
            let watchable = Set(
                snapshot.usage
                    .filter { $0.value.status == "ok" }
                    .map { $0.key }
            )
            let modes = self.runAutoPingCommand(["--get-autoping"])

            DispatchQueue.main.async {
                let known = self.knownWatchableProviders
                // The known set only ever grows. Antigravity in particular
                // reports usage only while its IDE runs, so a set that shrank
                // would rediscover it as "new" every time the IDE restarts.
                self.knownWatchableProviders = (known ?? []).union(watchable)

                guard let modes = modes,
                      let candidates = newcomers(known, watchable),
                      !candidates.isEmpty else { return }
                // Only speak up for a user who already wanted reminders
                // somewhere. With none configured, silence is their setting.
                guard modes.values.contains(where: { $0 != "off" }) else { return }

                let uncovered = candidates
                    .filter { (modes[$0] ?? "off") == "off" }
                    .sorted()
                guard !uncovered.isEmpty else { return }
                self.deliverReminderCoveragePrompt(for: uncovered)
            }
        }
    }

    private func deliverReminderCoveragePrompt(for providers: [String]) {
        // Named in the title, consequence in the body. Deliberately never says
        // "new agent" to the user: what it can prove is that this is the first
        // time the provider has been seen, which is not the same claim.
        let names = providers.map { PROVIDER_NAMES[$0] ?? $0.capitalized }
        let joined = names.joined(separator: L10n.tr("list.separator"))
        let title = names.count == 1
            ? L10n.tr("notification.reminder_coverage.title", names[0])
            : L10n.tr("notification.reminder_coverage.title_many")
        let body = names.count == 1
            ? L10n.tr("notification.reminder_coverage.body")
            : L10n.tr("notification.reminder_coverage.body_many", joined)

        deliverLocalNotification(
            title: title,
            body: body,
            userInfo: ["reminder_providers": providers],
            categoryIdentifier: AppDelegate.reminderPromptCategoryId
        )
    }

    /// Turns on weekly reminders for the providers the prompt named. Called
    /// from the notification's action button.
    func enableWeeklyReminders(for providers: [String]) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            for provider in providers {
                _ = self.setAutoPingMode(provider: provider, mode: "7d")
            }
            self.loadAutoPingModes()
        }
    }
}
