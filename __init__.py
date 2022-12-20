from mycroft import MycroftSkill, intent_file_handler


class Roon(MycroftSkill):
    def __init__(self):
        MycroftSkill.__init__(self)

    @intent_file_handler('roon.intent')
    def handle_roon(self, message):
        self.speak_dialog('roon')


def create_skill():
    return Roon()

