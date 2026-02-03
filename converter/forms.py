from django import forms

class VideoURLForm(forms.Form):
    url = forms.URLField(
        label="Video URL",
        widget=forms.URLInput(attrs={
            "placeholder": "Paste video URL here",
            "style": "width: 400px;"
        })
    )
