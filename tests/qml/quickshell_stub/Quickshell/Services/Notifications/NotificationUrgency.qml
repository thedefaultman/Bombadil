pragma Singleton
import QtQuick

// The three urgencies of the notification spec, by the names Quickshell's NotificationUrgency has.
QtObject {
    enum Urgency { Low = 0, Normal = 1, Critical = 2 }
}
