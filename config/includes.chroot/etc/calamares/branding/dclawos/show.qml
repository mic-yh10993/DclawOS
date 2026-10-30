import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation {
    id: presentation
    textColor: "#EAF6FF"
    titleColor: "#75D6FF"
    fontFamily: "WenQuanYi Zen Hei"

    Rectangle {
        anchors.fill: parent
        color: "#07162F"
        z: -1
    }

    function nextSlide() {
        presentation.goToNextSlide();
    }

    Timer {
        id: advanceTimer
        interval: 4200
        running: presentation.activatedInCalamares
        repeat: true
        onTriggered: nextSlide()
    }

    Slide {
        centeredText: qsTr("欢迎安装 DclawOS 2.0\nAI 操作系统：轻量 Openbox 桌面 + 底部任务栏 + AI 看板，基于 Debian 构建")
    }

    Slide {
        centeredText: qsTr("DclawOS\n基于 Debian Bookworm 构建\nAI 可操作整个系统，人类随时监视与叫停")
    }

    Slide {
        centeredText: qsTr("安装提示\n请先确认目标磁盘和分区方案，再开始安装。")
    }

    function onActivate() {
        presentation.currentSlide = 0;
    }

    function onLeave() {
        advanceTimer.stop();
    }
}
