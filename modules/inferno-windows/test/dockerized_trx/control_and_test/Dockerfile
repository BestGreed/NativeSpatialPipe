FROM python:3.13-alpine3.22

# pip needs to compile dependencies of netaudio
RUN apk add git build-base linux-headers sox
RUN cd / && git clone https://github.com/teodly/diff_tools && cd diff_tools && ./build_linux.sh

RUN CFLAGS="-Wno-int-conversion" pip install netaudio
ADD control_and_test.sh /
CMD [ "/control_and_test.sh" ]
