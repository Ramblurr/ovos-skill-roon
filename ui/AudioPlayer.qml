// roon-skill
// Copyright (C) 2022 Casey Link
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.
import QtQuick 2.12
import QtQuick.Window 2.12
import QtQuick.Layouts 1.12
import QtMultimedia 5.12
import QtGraphicalEffects 1.0
import QtQuick.Templates 2.12 as T
import QtQuick.Controls 2.12 as Controls
import org.kde.kirigami 2.11 as Kirigami
import Mycroft 1.0 as Mycroft

Mycroft.Delegate {
    id: root
    fillWidth: true
    skillBackgroundColorOverlay: "black"
    leftPadding: 0
    rightPadding: 0
    bottomPadding: 0
    topPadding: 0
    property int gridUnit: Mycroft.Units.gridUnit

    // Player Support Vertical / Horizontal Layouts
    // property int switchWidth: Kirigami.Units.gridUnit * 22
    // readonly property bool horizontal: width > switchWidth

    Rectangle {
        z: 100
        height: albumCoverContainer.width*0.8 //canvas.width * 0.16
        width: albumCoverContainer.width*0.8 // canvas.width * 0.16
        enabled: sessionData.hasAlbumCover
        visible: sessionData.hasAlbumCover
        anchors {
            left: canvas.left
            bottom: canvas.bottom
            bottomMargin: gridUnit * 3
            leftMargin: albumCoverContainer.width*0.15 //(canvas.width * 0.2) / 4
        }
        color: "transparent"
        Image {
            id: albumCover
            anchors.fill: parent
            source: sessionData.albumCoverUrl //Qt.resolvedUrl("images/album.jpg")
            z: 101
        }
        DropShadow {
            anchors.fill: albumCover
            source: albumCover
            horizontalOffset: 3
            verticalOffset: 3
            radius: 8.0
            samples: 17
            color: Qt.rgba(0.2, 0.2, 0.2, 0.60)
        }
    }

    ColumnLayout {
        id: canvas
        spacing: 0
        z: 2
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        Layout.margins: 0

        Rectangle {
            id: topArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "black"
            Image {
                id: artistArtBackground
                anchors.fill: parent
                fillMode: Image.Stretch
                source: sessionData.artistBlurredImageUrl
                enabled: sessionData.hasArtistBlurredImage
                visible: sessionData.hasArtistBlurredImage
            }
            FastBlur {
                id: blurredArtistBackground
                anchors.fill: artistArtBackground
                source: artistArtBackground
                radius: 100
                enabled: sessionData.hasArtistBlurredImage
                visible: sessionData.hasArtistBlurredImage
            }
            BrightnessContrast {
                anchors.fill: blurredArtistBackground
                source: blurredArtistBackground
                brightness: -0.8
                contrast: 0.0
                enabled: sessionData.artistBlurredImageDim
                visible: sessionData.artistBlurredImageDim
            }
            Image {
                id: artistArt
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                source: sessionData.artistImageUrl //Qt.resolvedUrl("images/artist.jpg")
                layer.effect: DropShadow {
                    spread: 0.3
                    radius: 8
                    color: Qt.rgba(0.2, 0.2, 0.2, 0.60)
                }
                enabled: sessionData.hasArtistImage
                visible: sessionData.hasArtistImage
            }
        }
        Rectangle {
            id: bottomArea
            color: "#2C2C2E"
            Layout.fillWidth: true
            height: gridUnit * 10
            RowLayout {
                spacing: 0
                anchors.fill: parent
                Rectangle {
                    id: albumCoverContainer
                    color: "transparent"
                    Layout.preferredWidth: bottomArea.width * 0.3
                    height: gridUnit * 10
                }
                ColumnLayout {
                    id: metadataArea
                    spacing: 0
                    Layout.leftMargin: gridUnit
                    Layout.fillWidth: true
                    Controls.Label {
                        id: line1
                        text: sessionData.line1
                        maximumLineCount: 1
                        Layout.fillWidth: true

                        font.bold: true
                        font.pixelSize: gridUnit * 2
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        color: "white"
                        visible: true
                        enabled: true
                    }
                    Controls.Label {
                        id: line12
                        text: sessionData.line2
                        maximumLineCount: 1
                        Layout.fillWidth: true

                        Layout.topMargin: -gridUnit * 0.5
                        font.pixelSize: gridUnit * 1.5
                        horizontalAlignment: Text.AlignLeft
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        color: "white"
                        visible: true
                        enabled: true
                    }
                    RowLayout {
                        spacing: 10
                        Layout.fillWidth: true
                        Controls.Label {
                            id: currentPosition
                            text: sessionData.currentPosition
                            maximumLineCount: 1
                            font.pixelSize: gridUnit
                            horizontalAlignment: Text.AlignLeft
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            color: "white"
                            Layout.preferredWidth: gridUnit * 4
                            visible: sessionData.hasProgress
                            enabled: sessionData.hasProgress
                        }
                        Controls.ProgressBar {
                            id: progressBar
                            value: sessionData.progressValue
                            Layout.fillWidth: true
                            Layout.preferredWidth: parent.width * 0.8
                            visible: sessionData.hasProgress
                            enabled: sessionData.hasProgress
                            background: Rectangle {
                                anchors.fill: progressBar
                                color: "#C6C7CA"
                                radius: 2
                            }
                            contentItem: Item {
                                implicitWidth: 200
                                implicitHeight: 10
                                Rectangle {
                                    width: progressBar.visualPosition * parent.width
                                    height: parent.height
                                    radius: 2
                                    color: "#7574F3"
                                }
                            }
                        }
                        Controls.Label {
                            id: duration
                            text: sessionData.duration
                            maximumLineCount: 1
                            font.pixelSize: gridUnit
                            horizontalAlignment: Text.AlignLeft
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                            Layout.preferredWidth: gridUnit * 4
                            color: "white"
                            visible: sessionData.hasProgress
                            enabled: sessionData.hasProgress
                        }
                    }
                }
            }
        }
    }
}
