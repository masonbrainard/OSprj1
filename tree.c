//Mason Brainard
//9/9/20
//section a

#define _XOPEN_SOURCE 500
#include <unistd.h>
#include <ftw.h>
#include <stdio.h>
#include <dirent.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <limits.h>

static int display_info(const char *fpath, const struct stat *sb, int tflag,
			struct FTW *ftwbuf)
{
	if (ftwbuf->level > 0 || tflag == FTW_F) {
		//how many tabs to add
		for (int i = 0; i < ftwbuf->level - 1; i = i + 1) {
			printf("%c", 9);
		}

		//what file/directory/sym
		if (tflag == FTW_D || tflag == FTW_F) {
			printf("%s", fpath + ftwbuf->base);
		} else if (tflag == FTW_SL) {
			//send to readlink function which will return the path
			char *buf;
			ssize_t nbytes, bufsiz;

			bufsiz = 4096;

			buf = malloc(bufsiz);

			nbytes = readlink(fpath, buf, bufsiz);
			if (nbytes == -1) {
				perror("readlink");
				exit(-1);
			}

			printf("SYM %s -> %s", fpath + ftwbuf->base, buf);

			free(buf);
		}
		printf("\n");
	}
	return 0;
}

int main(int argc, char *argv[])
{
    //check if input is a directory
    if (argc == 2)
    {
        DIR *dir;
        if ((dir = opendir(argv[1])) == NULL)
        {
            fprintf(stderr, "Error opening directory: %s are you sure it's a directory?\n", argv[1]);
            exit(1);
        }
        closedir(dir);
    }

	int flags = 0;

	flags |= FTW_PHYS;

	if (nftw((argc < 2) ? "." : argv[1], display_info, 20, flags) == -1) {
		exit(-1);
	}

	exit(0);
}
